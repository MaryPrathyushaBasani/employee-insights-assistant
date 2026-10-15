import io
import logging
import math
import os
import re
import smtplib
import ssl
from collections import Counter, OrderedDict
from datetime import datetime
from email.message import EmailMessage
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Flowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


MISSING_VALUES = {"", "null", "none", "n/a", "na", "n.a.", "-"}
SENSITIVE_COLUMN = re.compile(r"(^|_)(name|email|phone|mobile|telephone|contact|address|emp_id|employee_id)(_|$)", re.I)
NUMERIC_COLUMN = re.compile(
    r"salary|rating|amount|total|count|average|avg|mean|median|minimum|maximum|sum|revenue|cost|percent|rate|number|quantity|headcount|employees",
    re.I,
)
ID_COLUMN = re.compile(r"(^|_)(id|emp_id|employee_id)(_|$)", re.I)
DATE_COLUMN = re.compile(r"date|time|timestamp", re.I)
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
CHART_PALETTE = ["#176344", "#4d9472", "#80ad91", "#b0c9b7", "#d5e1d8", "#8a9b90"]
_REPORT_FONTS = None


def _report_fonts():
    global _REPORT_FONTS
    if _REPORT_FONTS is not None:
        return _REPORT_FONTS
    font_directory = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    font_files = {
        "regular": os.path.join(font_directory, "times.ttf"),
        "bold": os.path.join(font_directory, "timesbd.ttf"),
        "italic": os.path.join(font_directory, "timesi.ttf"),
        "bold_italic": os.path.join(font_directory, "timesbi.ttf"),
    }
    if all(os.path.isfile(path) for path in font_files.values()):
        names = {
            "regular": "TimesNewRoman",
            "bold": "TimesNewRoman-Bold",
            "italic": "TimesNewRoman-Italic",
            "bold_italic": "TimesNewRoman-BoldItalic",
        }
        for style, path in font_files.items():
            if names[style] not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(names[style], path))
        pdfmetrics.registerFontFamily(
            "TimesNewRoman", normal=names["regular"], bold=names["bold"],
            italic=names["italic"], boldItalic=names["bold_italic"],
        )
        _REPORT_FONTS = names
    else:
        _REPORT_FONTS = {
            "regular": "Times-Roman",
            "bold": "Times-Bold",
            "italic": "Times-Italic",
            "bold_italic": "Times-BoldItalic",
        }
    return _REPORT_FONTS


def is_sensitive_column(column):
    normalized = re.sub(r"[^a-z0-9]+", "_", str(column).strip().lower()).strip("_")
    return bool(SENSITIVE_COLUMN.search(normalized))


def safe_analysis_value(column, value):
    normalized = re.sub(r"[^a-z0-9]+", "_", str(column).strip().lower()).strip("_")
    if re.search(r"(^|_)status(_|$)", normalized) and isinstance(value, str):
        code = value.strip().upper()
        if code == "A":
            return "Active"
        if code in {"I", "T"}:
            return "Other status category"
    return value


def _missing(value):
    if value is None:
        return True
    return isinstance(value, str) and value.strip().casefold() in MISSING_VALUES


def _numeric_value(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"[\s,$€£₹]", "", value).rstrip("%")
    if not cleaned:
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _date_value(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        for date_format in ("%m/%d/%Y", "%d-%b-%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(candidate, date_format).date().isoformat()
            except ValueError:
                continue
    return None


def clean_query_result(result):
    """Return a normalized copy and quality counts without mutating query results."""
    columns = list(result.get("columns", []))
    source_rows = [list(row) for row in result.get("rows", [])]
    quality = {
        "missing_values": 0,
        "duplicate_rows": 0,
        "normalized_values": 0,
        "converted_types": 0,
        "source_rows": len(source_rows),
        "cleaned_rows": 0,
        "truncated": bool(result.get("truncated", False)),
        "source_unchanged": True,
    }
    source_counts = Counter(tuple(row) for row in source_rows)
    quality["duplicate_rows"] = sum(count - 1 for count in source_counts.values())
    if not columns or not source_rows:
        cleaned = dict(result)
        cleaned["rows"] = source_rows
        quality["cleaned_rows"] = len(source_rows)
        return cleaned, quality

    rows = [row[:len(columns)] + [None] * max(0, len(columns) - len(row)) for row in source_rows]
    for column_index, column in enumerate(columns):
        values = [row[column_index] for row in rows]
        cleaned_values = []
        for value in values:
            if _missing(value):
                cleaned_values.append(None)
                quality["missing_values"] += 1
            elif isinstance(value, str):
                normalized = " ".join(value.split())
                if normalized != value:
                    quality["normalized_values"] += 1
                cleaned_values.append(normalized)
            else:
                cleaned_values.append(value)

        normalized_column = re.sub(r"[^a-z0-9]+", "_", str(column).strip().lower()).strip("_")
        if DATE_COLUMN.search(normalized_column):
            parsed = [_date_value(value) if value is not None else None for value in cleaned_values]
            for row_index, (old, new) in enumerate(zip(cleaned_values, parsed)):
                if old is not None and new is None:
                    quality["missing_values"] += 1
                    quality["converted_types"] += 1
                elif old is not None and old != new:
                    quality["converted_types"] += 1
                cleaned_values[row_index] = new
                rows[row_index][column_index] = new
            continue

        nonempty = [value for value in cleaned_values if value is not None]
        numeric_values = [_numeric_value(value) for value in nonempty]
        numeric_successes = sum(value is not None for value in numeric_values)
        should_be_numeric = bool(NUMERIC_COLUMN.search(normalized_column)) and not ID_COLUMN.search(normalized_column)
        should_be_numeric = should_be_numeric or (
            not ID_COLUMN.search(normalized_column) and bool(nonempty) and numeric_successes / len(nonempty) >= 0.85
        )
        if should_be_numeric:
            parsed_values = [_numeric_value(value) if value is not None else None for value in cleaned_values]
            for row_index, (old, number) in enumerate(zip(cleaned_values, parsed_values)):
                if old is not None and number is None:
                    quality["missing_values"] += 1
                    quality["converted_types"] += 1
                elif old is not None and not isinstance(old, (int, float)):
                    quality["converted_types"] += 1
                rows[row_index][column_index] = int(number) if number is not None and number.is_integer() else number
            continue

        if normalized_column.endswith("email"):
            for row_index, value in enumerate(cleaned_values):
                if isinstance(value, str):
                    normalized = value.lower()
                    if normalized != value:
                        quality["normalized_values"] += 1
                    cleaned_values[row_index] = normalized
        else:
            spellings = [value for value in cleaned_values if isinstance(value, str)]
            counts = Counter(value.casefold() for value in spellings)
            canonical = {}

            def spelling_rank(value):
                if value.isupper() and len(value) <= 5:
                    return 0
                if value.istitle():
                    return 1
                if value.islower():
                    return 2
                return 3

            for value in spellings:
                key = value.casefold()
                if (
                    key not in canonical
                    or counts[key] > counts[canonical[key].casefold()]
                    or counts[key] == counts[canonical[key].casefold()]
                    and spelling_rank(value) < spelling_rank(canonical[key])
                ):
                    canonical[key] = value
            for row_index, value in enumerate(cleaned_values):
                if isinstance(value, str):
                    replacement = canonical[value.casefold()]
                    if replacement != value:
                        quality["normalized_values"] += 1
                    cleaned_values[row_index] = replacement
        for row_index, value in enumerate(cleaned_values):
            rows[row_index][column_index] = value

    quality["cleaned_rows"] = len(rows)
    cleaned = dict(result)
    cleaned["columns"] = columns
    cleaned["rows"] = rows
    cleaned["row_count"] = len(rows)
    return cleaned, quality


_DEPARTMENT_ALIASES = {
    "engineering": "Engineering",
    "finance": "Finance",
    "hr": "HR",
    "humanresources": "HR",
    "marketing": "Marketing",
    "operations": "Operations",
    "product": "Product",
    "productmanagement": "Product Management",
    "sales": "Sales",
    "support": "Support",
}


def normalize_department(value):
    if _missing(value):
        return "Missing department", True, False
    raw = " ".join(str(value).split())
    key = re.sub(r"[^a-z0-9]+", "", raw.casefold())
    canonical = _DEPARTMENT_ALIASES.get(key)
    if canonical:
        return canonical, raw != canonical, False
    return f"Needs review: {raw}", True, True


def build_employee_dataset(result):
    """Build shared employee metrics from raw rows without modifying the source."""
    columns = list(result.get("columns", []))
    source_rows = [list(row) for row in result.get("rows", [])]
    rows = [row[:len(columns)] + [None] * max(0, len(columns) - len(row)) for row in source_rows]
    indexes = {str(column).strip().casefold(): index for index, column in enumerate(columns)}

    def value(row, column):
        index = indexes.get(column)
        return row[index] if index is not None and index < len(row) else None

    missing_by_column = {
        str(column): sum(_missing(row[index]) for row in rows)
        for index, column in enumerate(columns)
    }
    raw_counts = Counter(
        "<missing>" if _missing(value(row, "department")) else str(value(row, "department"))
        for row in rows
    )
    normalized_counts = Counter()
    department_review_counts = Counter()
    department_standardization_records = 0
    for row in rows:
        raw_department = value(row, "department")
        normalized, changed, needs_review = normalize_department(raw_department)
        normalized_counts[normalized] += 1
        department_standardization_records += int(changed)
        if needs_review:
            review_label = "<missing>" if _missing(raw_department) else str(raw_department)
            department_review_counts[review_label] += 1

    exact_counts = Counter(tuple(row) for row in rows)
    exact_duplicate_rows = sum(count - 1 for count in exact_counts.values())
    unique_rows = []
    seen_rows = set()
    for row in rows:
        signature = tuple(row)
        if signature not in seen_rows:
            seen_rows.add(signature)
            unique_rows.append(row)

    rows_by_id = OrderedDict()
    missing_id_rows = 0
    for row in unique_rows:
        employee_id = value(row, "emp_id")
        if _missing(employee_id):
            missing_id_rows += 1
            continue
        rows_by_id.setdefault(employee_id, []).append(row)
    duplicate_id_groups = [group for group in rows_by_id.values() if len(group) > 1]
    eligible_rows = [group[0] for group in rows_by_id.values() if len(group) == 1]

    salary_values = []
    rating_values = []
    paired_rows = []
    valid_dates = []
    department_groups = OrderedDict()
    negative_salary_count = 0
    zero_salary_count = 0
    invalid_salary_count = 0
    invalid_rating_count = 0
    out_of_range_rating_count = 0
    unparseable_date_count = 0
    employee_missing_salary = 0
    employee_missing_rating = 0
    employee_missing_date = 0
    employee_unparseable_date = 0
    employee_negative_salary = 0
    employee_zero_salary = 0
    employee_out_of_range_rating = 0
    for row in rows:
        salary_value = value(row, "salary")
        rating_value = value(row, "rating")
        parsed_salary = _numeric_value(salary_value)
        parsed_rating = _numeric_value(rating_value)
        if not _missing(salary_value):
            if parsed_salary is None:
                invalid_salary_count += 1
            elif parsed_salary < 0:
                negative_salary_count += 1
            elif parsed_salary == 0:
                zero_salary_count += 1
        if not _missing(rating_value):
            if parsed_rating is None:
                invalid_rating_count += 1
            elif not 1 <= parsed_rating <= 5:
                out_of_range_rating_count += 1
        date_value = value(row, "join_date")
        if not _missing(date_value) and _date_value(date_value) is None:
            unparseable_date_count += 1

    for row in eligible_rows:
        salary_value = _numeric_value(value(row, "salary"))
        rating_value = _numeric_value(value(row, "rating"))
        date_value = _date_value(value(row, "join_date"))
        employee_missing_salary += int(_missing(value(row, "salary")))
        employee_missing_rating += int(_missing(value(row, "rating")))
        employee_missing_date += int(_missing(value(row, "join_date")))
        employee_negative_salary += int(salary_value is not None and salary_value < 0)
        employee_zero_salary += int(salary_value == 0)
        employee_out_of_range_rating += int(rating_value is not None and not 1 <= rating_value <= 5)
        employee_unparseable_date += int(not _missing(value(row, "join_date")) and date_value is None)
        if salary_value is not None and salary_value > 0:
            salary_values.append(salary_value)
        if rating_value is not None and 1 <= rating_value <= 5:
            rating_values.append(rating_value)
        if salary_value is not None and salary_value > 0 and rating_value is not None and 1 <= rating_value <= 5:
            paired_rows.append((salary_value, rating_value))
        if date_value:
            valid_dates.append(date_value)

        department, _, _ = normalize_department(value(row, "department"))
        group = department_groups.setdefault(department, {
            "department": department,
            "employee_count": 0,
            "salary_values": [],
            "rating_values": [],
        })
        group["employee_count"] += 1
        if salary_value is not None and salary_value > 0:
            group["salary_values"].append(salary_value)
        if rating_value is not None and 1 <= rating_value <= 5:
            group["rating_values"].append(rating_value)

    department_metrics = []
    department_salary_values = {}
    for group in department_groups.values():
        salaries = group.pop("salary_values")
        ratings = group.pop("rating_values")
        department_salary_values[group["department"]] = list(salaries)
        group["valid_salary_observations"] = len(salaries)
        group["average_salary"] = sum(salaries) / len(salaries) if salaries else None
        group["valid_rating_observations"] = len(ratings)
        group["average_rating"] = sum(ratings) / len(ratings) if ratings else None
        department_metrics.append(group)

    quality = {
        "source_rows": len(rows),
        "exact_duplicate_rows": exact_duplicate_rows,
        "distinct_employee_ids": len(rows_by_id),
        "duplicate_employee_id_count": len(duplicate_id_groups),
        "duplicate_employee_extra_rows": sum(len(group) - 1 for group in duplicate_id_groups),
        "rows_with_reused_employee_ids": sum(len(group) for group in duplicate_id_groups),
        "records_excluded_for_reused_employee_ids": sum(len(group) for group in duplicate_id_groups),
        "records_with_missing_employee_id": missing_id_rows,
        "employee_rows_used_for_metrics": len(eligible_rows),
        "valid_salary_observations": len(salary_values),
        "valid_rating_observations": len(rating_values),
        "valid_salary_rating_pairs": len(paired_rows),
        "average_salary": sum(salary_values) / len(salary_values) if salary_values else None,
        "average_rating": sum(rating_values) / len(rating_values) if rating_values else None,
        "negative_salary_values": negative_salary_count,
        "zero_salary_values": zero_salary_count,
        "invalid_salary_values": invalid_salary_count,
        "invalid_rating_values": invalid_rating_count,
        "out_of_range_rating_values": out_of_range_rating_count,
        "employee_missing_salary": employee_missing_salary,
        "employee_missing_rating": employee_missing_rating,
        "employee_missing_joining_date": employee_missing_date,
        "employee_negative_salary_values": employee_negative_salary,
        "employee_zero_salary_values": employee_zero_salary,
        "employee_out_of_range_rating_values": employee_out_of_range_rating,
        "employee_unparseable_joining_dates": employee_unparseable_date,
        "valid_joining_dates": len(valid_dates),
        "unparseable_joining_dates": unparseable_date_count,
        "missing_by_column": missing_by_column,
        "raw_department_counts": dict(raw_counts),
        "normalized_department_counts": dict(normalized_counts),
        "records_needing_department_standardization": department_standardization_records,
        "department_mapping_review_counts": dict(department_review_counts),
    }
    return {
        "quality": quality,
        "salary_values": salary_values,
        "rating_values": rating_values,
        "salary_rating_pairs": paired_rows,
        "valid_joining_dates": valid_dates,
        "department_metrics": department_metrics,
        "department_salary_values": department_salary_values,
        "truncated": bool(result.get("truncated", False)),
    }


def build_employee_chart(dataset, chart_type, question=""):
    if chart_type == "histogram":
        values = dataset["salary_values"]
        return build_chart({
            "columns": ["salary"],
            "rows": [[value] for value in values],
        }, "histogram")

    if chart_type == "correlation_heatmap":
        pairs = dataset["salary_rating_pairs"]
        if len(pairs) < 2:
            return None
        salaries = [pair[0] for pair in pairs]
        ratings = [pair[1] for pair in pairs]
        salary_mean = sum(salaries) / len(salaries)
        rating_mean = sum(ratings) / len(ratings)
        covariance = sum((salary - salary_mean) * (rating - rating_mean) for salary, rating in pairs)
        salary_variance = sum((salary - salary_mean) ** 2 for salary in salaries)
        rating_variance = sum((rating - rating_mean) ** 2 for rating in ratings)
        denominator = math.sqrt(salary_variance * rating_variance)
        if denominator == 0:
            return None
        correlation = max(-1.0, min(1.0, covariance / denominator))
        return {
            "type": "heatmap",
            "title": "Correlation between salary and rating",
            "metric": "Pearson correlation coefficient",
            "dimension": "Salary and rating",
            "unit": "r",
            "labels": [],
            "values": [],
            "x_labels": ["Salary", "Rating"],
            "y_labels": ["Salary", "Rating"],
            "matrix": [[1.0, round(correlation, 4)], [round(correlation, 4), 1.0]],
            "x_label": "Measure",
            "y_label": "Measure",
            "observations": len(pairs),
        }

    if chart_type == "salary_box":
        result = {
            "columns": ["department", "salary"],
            "rows": [
                [department, salary]
                for department, salaries in dataset["department_salary_values"].items()
                for salary in salaries
            ],
        }
        return build_chart(result, "box")

    if chart_type == "department_metrics":
        lowered = str(question).casefold()
        metric = "average_salary" if "salary" in lowered else "average_rating" if "rating" in lowered else "employee_count"
        rows = [
            [item["department"], item[metric]]
            for item in dataset["department_metrics"]
            if item[metric] is not None
        ]
        return build_chart({
            "columns": ["department", metric],
            "rows": rows,
            "question": question,
        }, requested_chart_type(question))

    if chart_type == "monthly_hiring":
        monthly_counts = Counter(value[:7] for value in dataset["valid_joining_dates"])
        if not monthly_counts:
            return None
        last_year, last_month = map(int, max(monthly_counts).split("-"))
        months = []
        for offset in range(11, -1, -1):
            month_index = last_year * 12 + last_month - 1 - offset
            year, month_index = divmod(month_index, 12)
            months.append(f"{year:04d}-{month_index + 1:02d}")
        result = {
            "columns": ["join_date", "employee_count"],
            "rows": [[month, monthly_counts.get(month, 0)] for month in months],
            "question": "monthly hiring trend",
        }
        chart = build_chart(result, "line")
        if chart:
            chart["title"] = "Monthly hires (12 most recent months)"
            chart["x_label"] = "Month"
            chart["y_label"] = "Hires"
            chart["unit"] = "employees"
            chart["observations"] = len(dataset["valid_joining_dates"])
        return chart

    return None


def build_employee_analysis(question, intent, dataset):
    quality = dataset["quality"]
    employee_count = quality["employee_rows_used_for_metrics"]
    duplicate_rows = quality["rows_with_reused_employee_ids"]
    limitations = []
    tables = []
    chart = None
    chart_notice = None
    chart_failure_type = None
    salary_average = quality["average_salary"]
    rating_average = quality["average_rating"]

    def make_chart(chart_type):
        nonlocal chart_failure_type
        try:
            return build_employee_chart(dataset, chart_type, question)
        except Exception as error:
            chart_failure_type = type(error).__name__
            return None

    if quality["duplicate_employee_id_count"]:
        limitations.append(
            f"{duplicate_rows} distinct source records share a reused employee ID; all were excluded from employee-level metrics."
        )
    if dataset["truncated"]:
        limitations.append("The employee source result was truncated, so counts and charts may not cover the complete source.")

    if intent == "count_reconciliation":
        answer = (
            f"The full source has {quality['source_rows']:,} rows but {quality['distinct_employee_ids']:,} distinct employee IDs; "
            f"one ID is reused across {duplicate_rows:,} differing records, leaving {employee_count:,} unambiguous employees. "
            f"Validated counts are {quality['valid_salary_observations']:,} valid salaries, {quality['valid_rating_observations']:,} valid ratings, "
            f"and {quality['valid_salary_rating_pairs']:,} valid pairs, so 492 is not reproduced by these full-source rules."
        )
        insights = [
            f"The source-row count is {quality['source_rows']:,}; distinct employee IDs number {quality['distinct_employee_ids']:,}.",
            f"Employee-level metrics exclude {duplicate_rows:,} records tied to the reused ID and use {employee_count:,} unambiguous records.",
            "The exact query or filter behind 492 is not available here, so its additional exclusions cannot be verified.",
        ]
        tables.append({
            "title": "Full-source count reconciliation",
            "columns": ["Population", "Count", "Definition"],
            "rows": [
                ["Source rows", quality["source_rows"], "All source records returned by the read-only full-table query."],
                ["Distinct employee IDs", quality["distinct_employee_ids"], "Unique nonmissing emp_id values."],
                ["Rows under reused IDs", duplicate_rows, "Differing source records for IDs occurring more than once; excluded from employee-level metrics."],
                ["Employees used for metrics", employee_count, "Distinct IDs with one distinct source record."],
                ["Valid salary observations", quality["valid_salary_observations"], "Positive finite salaries from the employee-level population."],
                ["Valid rating observations", quality["valid_rating_observations"], "Ratings from 1 through 5 inclusive."],
                ["Valid salary-rating pairs", quality["valid_salary_rating_pairs"], "Employees with both measures valid."],
                ["Previously cited count", 492, "Not reproduced by these full-source rules; the prior filter is needed to explain it."],
            ],
        })
        limitations.append("The exact earlier SQL/filter that produced 492 is not available, so no specific exclusion can be assigned to that value.")

    elif intent == "data_quality":
        answer = (
            f"The source contains {quality['source_rows']:,} rows and {quality['distinct_employee_ids']:,} distinct employee IDs. "
            f"There are {quality['exact_duplicate_rows']:,} exact duplicate rows; one ID is reused across "
            f"{duplicate_rows:,} distinct records, which are excluded from employee-level metrics."
        )
        overview = [
            ["Source rows", quality["source_rows"], "Physical source records before duplicate handling."],
            ["Distinct employee IDs", quality["distinct_employee_ids"], "Distinct nonmissing emp_id values; a repeated ID counts once."],
            ["Exact duplicate rows", quality["exact_duplicate_rows"], "Extra full rows identical across every selected source field."],
            ["Repeated-ID groups", quality["duplicate_employee_id_count"], "Distinct employee IDs appearing in multiple different records."],
            ["Rows under repeated IDs", duplicate_rows, "All rows for reused IDs, excluded to avoid selecting an arbitrary employee record."],
            ["Extra rows under repeated IDs", quality["duplicate_employee_extra_rows"], "Rows beyond one per repeated employee ID."],
            ["Employees used for metrics", employee_count, "Nonmissing IDs with one distinct source record after exact-row deduplication."],
            ["Valid salary observations", quality["valid_salary_observations"], "Finite salaries greater than zero; missing, invalid, zero, and negative values are excluded."],
            ["Valid rating observations", quality["valid_rating_observations"], "Finite ratings from 1 through 5 inclusive."],
            ["Valid salary-rating pairs", quality["valid_salary_rating_pairs"], "Employee records with both a valid salary and a valid rating."],
            ["Valid joining dates", quality["valid_joining_dates"], "Employee records whose nonmissing joining date parses successfully."],
            ["Negative salary values", quality["negative_salary_values"], "Raw source salary values below zero; never replaced with zero."],
            ["Zero salary values", quality["zero_salary_values"], "Raw source salary values equal to zero; excluded from valid salary metrics."],
            ["Out-of-range ratings", quality["out_of_range_rating_values"], "Raw numeric ratings outside the inclusive 1–5 range."],
            ["Unparseable joining dates", quality["unparseable_joining_dates"], "Nonmissing source date values that could not be parsed."],
            ["Records needing department standardization", quality["records_needing_department_standardization"], "Rows changed to a canonical label or explicitly flagged for review."],
            ["Department records flagged for review", sum(quality["department_mapping_review_counts"].values()), "Rows with missing or uncertain department labels; not merged into a guessed department."],
        ]
        tables.append({
            "title": "Quality measures and definitions",
            "columns": ["Measure", "Count", "Definition"],
            "rows": overview,
        })
        tables.append({
            "title": "Missing values by column",
            "columns": ["Column", "Missing count", "Definition"],
            "rows": [[column, count, "Null, blank, or recognized missing-value marker in source rows."]
                     for column, count in quality["missing_by_column"].items()],
        })
        department_rows = []
        for raw_label, raw_count in sorted(quality["raw_department_counts"].items(), key=lambda item: item[0].casefold()):
            raw_value = None if raw_label == "<missing>" else raw_label
            normalized, _, needs_review = normalize_department(raw_value)
            department_rows.append([
                raw_label,
                raw_count,
                normalized,
                quality["normalized_department_counts"].get(normalized, 0),
                "Needs review" if needs_review else "Standardized",
            ])
        tables.append({
            "title": "Raw and normalized department counts",
            "columns": ["Raw label", "Raw rows", "Normalized label", "Normalized rows", "Mapping"],
            "rows": department_rows,
        })
        limitations.append(
            "Department normalization merges only listed, unambiguous names and formatting variants; labels marked Needs review remain separate."
        )
        insights = [
            f"{quality['exact_duplicate_rows']:,} exact duplicate rows were found; {quality['duplicate_employee_id_count']:,} employee-ID group(s) have differing records.",
            f"{quality['records_needing_department_standardization']:,} source records need department standardization; uncertain labels are flagged instead of merged.",
            f"Valid salary and rating populations are {quality['valid_salary_observations']:,} and {quality['valid_rating_observations']:,} employees, respectively.",
        ]
        recommendations = ["Review the flagged department labels and reused employee ID before treating those records as canonical employee history."]

    elif intent == "histogram":
        chart = make_chart("histogram")
        if not dataset["salary_values"]:
            answer = "No valid positive salary observations are available for a histogram."
            chart_notice = "A salary histogram requires at least one finite salary greater than zero."
            insights = []
        else:
            answer = (
                f"The salary histogram uses {quality['valid_salary_observations']:,} valid observations from "
                f"{employee_count:,} unambiguous employees. The mean salary is {salary_average:,.2f} "
                "across those valid observations."
            )
            insights = [
                f"{quality['valid_salary_observations']:,} salaries are finite and greater than zero.",
                f"Mean salary is {salary_average:,.2f}, calculated directly from valid employee records.",
                f"{quality['employee_negative_salary_values'] + quality['employee_zero_salary_values']:,} salary values were excluded because they were negative or zero; missing values were not imputed.",
            ]
            if chart:
                tables.append({
                    "title": "Salary histogram bins",
                    "columns": ["Salary interval", "Employee observations"],
                    "rows": [[label, count] for label, count in zip(chart["labels"], chart["values"])],
                })

    elif intent == "salary_box":
        chart = make_chart("salary_box")
        if not dataset["salary_values"]:
            answer = "No valid positive salary observations are available for a box plot."
            chart_notice = "A salary box plot requires at least one finite salary greater than zero."
            insights = []
        else:
            answer = (
                f"The salary box plot uses {quality['valid_salary_observations']:,} valid observations "
                f"from {employee_count:,} unambiguous employees; quartiles and 1.5-IQR whiskers are computed from raw salaries."
            )
            insights = [
                f"{quality['valid_salary_observations']:,} positive finite salaries are included.",
                "Whiskers stop at the most extreme values within 1.5 times the interquartile range; outside values are shown as outliers.",
            ]
            if chart:
                tables.append({
                    "title": "Salary quartiles by normalized department",
                    "columns": ["Department", "Lower whisker", "Q1", "Median", "Q3", "Upper whisker", "Outlier count"],
                    "rows": [[
                        group["label"], group["min"], group["q1"], group["median"], group["q3"],
                        group["max"], len(group["outliers"]),
                    ] for group in chart["groups"]],
                })

    elif intent == "department_metrics":
        chart = make_chart("department_metrics")
        answer = (
            f"Department metrics use {employee_count:,} unambiguous employees across "
            f"{len(dataset['department_metrics']):,} normalized or review-flagged labels. "
            f"{quality['records_needing_department_standardization']:,} source records need standardization; uncertain labels remain separate."
        )
        insights = [
            f"The department table includes {employee_count:,} employee IDs with one distinct source record each.",
            f"{sum(quality['department_mapping_review_counts'].values()):,} source records carry missing or uncertain department labels and are flagged for review.",
            "Department salary and rating averages use valid individual employee observations and their own denominators.",
        ]
        tables.append({
            "title": "Department metrics after normalization",
            "columns": ["Department", "Employees", "Valid salaries", "Mean salary", "Valid ratings", "Mean rating"],
            "rows": [[
                item["department"], item["employee_count"], item["valid_salary_observations"],
                item["average_salary"], item["valid_rating_observations"], item["average_rating"],
            ] for item in dataset["department_metrics"]],
        })
        limitations.append("Department labels marked Needs review are kept separate rather than merged using an unverified alias.")

    elif intent == "correlation_heatmap":
        pairs = dataset["salary_rating_pairs"]
        coefficient = None
        if len(pairs) >= 2:
            salaries = [pair[0] for pair in pairs]
            ratings = [pair[1] for pair in pairs]
            salary_mean = sum(salaries) / len(salaries)
            rating_mean = sum(ratings) / len(ratings)
            covariance = sum((salary - salary_mean) * (rating - rating_mean) for salary, rating in pairs)
            denominator = math.sqrt(
                sum((salary - salary_mean) ** 2 for salary in salaries)
                * sum((rating - rating_mean) ** 2 for rating in ratings)
            )
            if denominator:
                coefficient = max(-1.0, min(1.0, covariance / denominator))
        chart = make_chart("correlation_heatmap")
        if coefficient is None:
            answer = "A salary-rating correlation could not be estimated from the available valid paired observations."
            chart_notice = "Pearson correlation needs at least two paired valid observations and variation in both measures."
            insights = []
        else:
            answer = (
                f"Pearson correlation between salary and rating is {coefficient:.4f}, based on "
                f"{quality['valid_salary_rating_pairs']:,} employees with both measures valid."
            )
            insights = [
                f"The paired population is {quality['valid_salary_rating_pairs']:,} of {employee_count:,} unambiguous employees.",
                "Correlation describes association only; it does not establish that salary causes a rating change or vice versa.",
            ]
            if chart:
                tables.append({
                    "title": "Salary-rating correlation matrix",
                    "columns": ["Measure", *chart["x_labels"]],
                    "rows": [[label, *values] for label, values in zip(chart["y_labels"], chart["matrix"])],
                })
        limitations.append("Only employees with a positive finite salary and a rating from 1 through 5 are included in the paired calculation.")

    elif intent == "monthly_hiring":
        chart = make_chart("monthly_hiring")
        dates = dataset["valid_joining_dates"]
        if not dates:
            answer = "No parseable joining dates are available for a monthly hiring trend."
            chart_notice = "A monthly hiring trend requires at least one parseable joining date."
            insights = []
        else:
            answer = (
                f"The monthly hiring trend includes {quality['valid_joining_dates']:,} parseable dates from "
                f"{employee_count:,} unambiguous employees; {quality['employee_missing_joining_date'] + quality['employee_unparseable_joining_dates']:,} records were excluded for missing or unparseable dates."
            )
            insights = [
                f"The latest parseable joining date is in {max(dates)[:7]}; the chart covers the 12 months ending there.",
                f"{quality['valid_joining_dates']:,} employee joining dates are parseable in total.",
            ]
            if chart:
                tables.append({
                    "title": "Monthly hires shown in chart",
                    "columns": ["Month", "Hires"],
                    "rows": [[label, count] for label, count in zip(chart["labels"], chart["values"])],
                })
        limitations.append("The trend uses parseable dates only and displays the 12 calendar months ending at the latest valid date.")

    else:
        paired = quality["valid_salary_rating_pairs"]
        answer = (
            f"Across unambiguous employees, mean salary is {salary_average:,.2f} "
            f"(n={quality['valid_salary_observations']:,}) and mean rating is {rating_average:.2f} "
            f"(n={quality['valid_rating_observations']:,}); {paired:,} employees have both values valid."
        )
        insights = [
            f"Valid salary observations: {quality['valid_salary_observations']:,} of {employee_count:,} employees.",
            f"Valid rating observations: {quality['valid_rating_observations']:,} of {employee_count:,} employees.",
            "Each overall average is calculated directly from its valid employee observations, not from department averages.",
        ]
        tables.append({
            "title": "Overall salary and rating",
            "columns": ["Measure", "Average", "Valid employee observations"],
            "rows": [
                ["Salary", salary_average, quality["valid_salary_observations"]],
                ["Rating (1–5)", rating_average, quality["valid_rating_observations"]],
                ["Employees with both measures", "Not an average", paired],
            ],
        })
        department_rows = [[
            item["department"], item["employee_count"], item["valid_salary_observations"],
            item["average_salary"], item["valid_rating_observations"], item["average_rating"],
        ] for item in dataset["department_metrics"]]
        tables.append({
            "title": "Department metrics after normalization",
            "columns": ["Department", "Employees", "Valid salaries", "Mean salary", "Valid ratings", "Mean rating"],
            "rows": department_rows,
        })
        limitations.append("Department labels marked Needs review are kept separate, so their department-level averages remain provisional.")

    if chart_failure_type:
        chart_notice = "The analysis is ready, but the requested chart could not be generated."
        limitations.append(chart_notice)

    analysis = {
        "answer": answer,
        "insights": insights[:3],
        "possible_explanations": [],
        "implications": [],
        "recommendations": recommendations if intent == "data_quality" else [],
        "conclusion": answer,
        "tables": tables,
        "limitations": limitations,
        "data_quality": quality,
    }
    if chart_failure_type:
        analysis["_chart_error_type"] = chart_failure_type
    if intent == "correlation_heatmap" and chart:
        limitations.append("Correlation is an association measure and does not establish causation.")
    return analysis, chart, chart_notice


def employee_analysis_intent(question):
    text = str(question or "").casefold()
    if "895" in text and "492" in text:
        return "count_reconciliation"
    if re.search(r"\b(data quality|quality check|missing values?|duplicate rows?|standardiz\w+)\b", text):
        return "data_quality"
    if "histogram" in text:
        return "histogram"
    if re.search(r"\bbox\s*[- ]?plots?\b|\bboxplots?\b", text):
        return "salary_box"
    if "correlat" in text and ("heatmap" in text or "heat map" in text):
        return "correlation_heatmap"
    if ("monthly" in text or "month-by-month" in text) and re.search(r"\b(hir\w*|join\w*|employ\w*)\b", text):
        return "monthly_hiring"
    if "department" in text and re.search(r"\b(employee|headcount|count|salary|rating|pay)\w*\b", text):
        return "department_metrics"
    if "salary" in text and "rating" in text and re.search(r"\b(average|averages|compare|comparison|relationship|correlat)\b", text):
        return "salary_rating"
    return None


def safe_analysis_rows(result, limit=80):
    columns = result.get("columns", [])
    visible_indexes = [index for index, column in enumerate(columns) if not is_sensitive_column(column)]
    visible_columns = [columns[index] for index in visible_indexes]
    rows = [
        [safe_analysis_value(columns[index], row[index]) if index < len(row) else None for index in visible_indexes]
        for row in result.get("rows", [])[:limit]
    ]
    return visible_columns, rows


def build_chart(result, requested_type=None):
    columns = result.get("columns", [])
    rows = result.get("rows", [])

    numeric_indexes = []
    category_indexes = []
    for index, column in enumerate(columns):
        normalized = re.sub(r"[^a-z0-9]+", "_", str(column).strip().lower()).strip("_")
        if is_sensitive_column(column) or ID_COLUMN.search(normalized):
            continue
        values = [row[index] for row in rows if index < len(row) and row[index] is not None]
        if not values:
            continue
        if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            numeric_indexes.append(index)
        elif all(isinstance(value, str) for value in values):
            category_indexes.append(index)
    requested_type = requested_type or requested_chart_type(result.get("question", ""))
    numeric_values = {
        index: [float(value) for row in rows if index < len(row)
                and isinstance((value := row[index]), (int, float))
                and not isinstance(value, bool) and math.isfinite(float(value))]
        for index in numeric_indexes
    }
    numeric_indexes = [index for index in numeric_indexes if numeric_values[index]]
    date_indexes = [index for index in category_indexes if DATE_COLUMN.search(str(columns[index]))]
    default_metric = numeric_indexes[0] if numeric_indexes else None

    def name(index):
        return re.sub(r"[_\s]+", " ", str(columns[index])).strip().title()

    def base(chart_type, title, labels, values, metric_index=None, dimension_index=None, **extra):
        metric = name(metric_index) if metric_index is not None else "Frequency"
        dimension = name(dimension_index) if dimension_index is not None else metric
        unit = _chart_unit(metric)
        return {
            "type": chart_type,
            "title": title[:100],
            "metric": metric,
            "dimension": dimension,
            "unit": unit,
            "labels": labels,
            "values": values,
            **extra,
        }

    def grouped_values(dimension_index, metric_index):
        totals = OrderedDict()
        for row in rows:
            if dimension_index >= len(row) or metric_index >= len(row):
                continue
            label = safe_analysis_value(columns[dimension_index], row[dimension_index])
            value = row[metric_index]
            if label is None or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                continue
            label = str(label).strip()[:72]
            totals[label] = totals.get(label, 0.0) + float(value)
        return totals

    def numeric_column(index):
        return numeric_values.get(index, [])

    if requested_type == "scatter":
        if len(numeric_indexes) < 2 or len(rows) < 3:
            return None
        x_index, y_index = numeric_indexes[:2]
        points = [
            {"x": float(row[x_index]), "y": float(row[y_index])}
            for row in rows
            if len(row) > max(x_index, y_index)
            and isinstance(row[x_index], (int, float)) and math.isfinite(float(row[x_index]))
            and isinstance(row[y_index], (int, float)) and math.isfinite(float(row[y_index]))
        ]
        if len(points) < 3:
            return None
        return base("scatter", f"{name(y_index)} vs. {name(x_index)}", [], [], y_index,
                    x_label=name(x_index), y_label=name(y_index), x_unit=_chart_unit(name(x_index)),
                    y_unit=_chart_unit(name(y_index)), points=points[:300])

    if requested_type is None and not category_indexes and len(numeric_indexes) >= 2 and len(rows) >= 3:
        return build_chart(result, "scatter")

    if requested_type == "histogram" or (requested_type is None and not category_indexes and default_metric is not None):
        if default_metric is None or not numeric_column(default_metric):
            return None
        values = numeric_column(default_metric)
        minimum, maximum = min(values), max(values)
        if minimum == maximum:
            chart = base("histogram", f"Distribution of {name(default_metric)}", [f"{minimum:,.3g}"], [len(values)],
                         default_metric, bin_edges=[minimum, maximum], x_label=name(default_metric),
                         y_label="Number of records")
            chart["unit"] = "records"
            chart["x_unit"] = _chart_unit(name(default_metric))
            return chart
        bin_count = min(10, max(4, math.ceil(math.sqrt(len(values)))))
        bin_width = (maximum - minimum) / bin_count
        bins = [0] * bin_count
        for value in values:
            bins[min(int((value - minimum) / bin_width), bin_count - 1)] += 1
        labels = [f"{minimum + index * bin_width:,.3g}–{minimum + (index + 1) * bin_width:,.3g}" for index in range(bin_count)]
        chart = base("histogram", f"Distribution of {name(default_metric)}", labels, bins,
                 default_metric, bin_edges=[round(minimum + index * bin_width, 4) for index in range(bin_count + 1)],
                 x_label=name(default_metric), y_label="Number of records")
        chart["unit"] = "records"
        chart["x_unit"] = _chart_unit(name(default_metric))
        return chart

    if requested_type == "box":
        if default_metric is None or not numeric_column(default_metric):
            return None
        candidate_categories = [index for index in category_indexes if index not in date_indexes]
        groups = OrderedDict()
        if candidate_categories:
            category_index = candidate_categories[0]
            for row in rows:
                if len(row) <= max(default_metric, category_index):
                    continue
                label = safe_analysis_value(columns[category_index], row[category_index])
                value = row[default_metric]
                if label is not None and isinstance(value, (int, float)) and math.isfinite(float(value)):
                    groups.setdefault(str(label)[:48], []).append(float(value))
        else:
            groups["All results"] = numeric_column(default_metric)
        groups = OrderedDict((label, values) for label, values in groups.items() if values)
        if not groups:
            return None
        summaries = []
        for label, values in list(groups.items())[:8]:
            ordered = sorted(values)
            first_quartile = _percentile(ordered, 0.25)
            third_quartile = _percentile(ordered, 0.75)
            interquartile_range = third_quartile - first_quartile
            lower_fence = first_quartile - 1.5 * interquartile_range
            upper_fence = third_quartile + 1.5 * interquartile_range
            inlier_values = [value for value in ordered if lower_fence <= value <= upper_fence]
            summaries.append({
                "label": label,
                "min": inlier_values[0],
                "q1": first_quartile,
                "median": _percentile(ordered, 0.5),
                "q3": third_quartile,
                "max": inlier_values[-1],
                "outliers": [value for value in ordered if value < lower_fence or value > upper_fence],
            })
        return base("box", f"Distribution of {name(default_metric)}", [], [], default_metric,
                    groups=summaries, x_label=name(default_metric), y_label="Group")

    if requested_type == "heatmap":
        question = str(result.get("question", "")).casefold()
        if "correlat" in question and len(numeric_indexes) >= 2:
            labels = [name(index) for index in numeric_indexes[:10]]
            matrix = []
            for y_index in numeric_indexes[:10]:
                matrix_row = []
                for x_index in numeric_indexes[:10]:
                    pairs = [
                        (float(row[x_index]), float(row[y_index]))
                        for row in rows
                        if len(row) > max(x_index, y_index)
                        and isinstance(row[x_index], (int, float)) and not isinstance(row[x_index], bool)
                        and isinstance(row[y_index], (int, float)) and not isinstance(row[y_index], bool)
                        and math.isfinite(float(row[x_index])) and math.isfinite(float(row[y_index]))
                    ]
                    if len(pairs) < 2:
                        return None
                    x_values = [pair[0] for pair in pairs]
                    y_values = [pair[1] for pair in pairs]
                    x_mean = sum(x_values) / len(x_values)
                    y_mean = sum(y_values) / len(y_values)
                    numerator = sum((x - x_mean) * (y - y_mean) for x, y in pairs)
                    denominator = math.sqrt(
                        sum((x - x_mean) ** 2 for x in x_values)
                        * sum((y - y_mean) ** 2 for y in y_values)
                    )
                    if denominator == 0:
                        return None
                    matrix_row.append(round(max(-1.0, min(1.0, numerator / denominator)), 4))
                matrix.append(matrix_row)
            return base("heatmap", "Numeric measure correlation heatmap", [], [], numeric_indexes[0],
                        x_labels=labels, y_labels=labels, matrix=matrix,
                        x_label="Measure", y_label="Measure", unit="r")
        if len(category_indexes) < 2 or default_metric is None:
            return None
        x_index, y_index = category_indexes[:2]
        x_labels = []
        y_labels = []
        cells = {}
        for row in rows:
            if len(row) <= max(x_index, y_index, default_metric):
                continue
            x_label = safe_analysis_value(columns[x_index], row[x_index])
            y_label = safe_analysis_value(columns[y_index], row[y_index])
            value = row[default_metric]
            if x_label is None or y_label is None or not isinstance(value, (int, float)):
                continue
            x_label, y_label = str(x_label)[:32], str(y_label)[:32]
            if x_label not in x_labels:
                x_labels.append(x_label)
            if y_label not in y_labels:
                y_labels.append(y_label)
            cells[(y_label, x_label)] = cells.get((y_label, x_label), 0.0) + float(value)
        x_labels, y_labels = x_labels[:10], y_labels[:10]
        if len(x_labels) < 2 or len(y_labels) < 2:
            return None
        return base("heatmap", f"{name(default_metric)} by {name(y_index)} and {name(x_index)}", [], [], default_metric,
                    x_labels=x_labels, y_labels=y_labels,
                    matrix=[[round(cells.get((y, x), 0.0), 4) for x in x_labels] for y in y_labels],
                    x_label=name(x_index), y_label=name(y_index))

    if not numeric_indexes or not category_indexes:
        if requested_type is None and len(numeric_indexes) >= 2 and len(rows) >= 3:
            return build_chart(result, "scatter")
        return None

    dimension_index = date_indexes[0] if date_indexes else category_indexes[0]
    metric_index = default_metric
    totals = grouped_values(dimension_index, metric_index)
    if len(totals) < 2:
        return None
    dimension_is_date = dimension_index in date_indexes
    items = sorted(totals.items()) if dimension_is_date else sorted(totals.items(), key=lambda item: item[1], reverse=True)
    items = items[:12]

    if requested_type == "heatmap":
        return None
    if requested_type in {"scatter", "histogram", "box"}:
        return None
    if requested_type in {"line", "area"} and not dimension_is_date:
        return None
    if requested_type == "pie" and (len(items) > 8 or any(value < 0 for _, value in items) or sum(value for _, value in items) <= 0):
        return None

    metric_key = re.sub(r"[^a-z0-9]+", "_", str(columns[metric_index]).lower()).strip("_")
    composition = bool(re.search(r"(^|_)(count|headcount|employees|employee_count|share|percent|percentage|proportion)(_|$)", metric_key))
    if requested_type:
        chart_type = requested_type
    elif dimension_is_date:
        chart_type = "line"
    elif len(items) <= 6 and composition and all(value >= 0 for _, value in items) and sum(value for _, value in items) > 0:
        chart_type = "donut"
    else:
        chart_type = "column" if len(items) <= 6 else "bar"
    return base(chart_type, f"{name(metric_index)} {'over' if dimension_is_date else 'by'} {name(dimension_index)}",
                [item[0] for item in items], [round(item[1], 4) for item in items], metric_index, dimension_index,
                x_label=name(dimension_index), y_label=name(metric_index))


def _chart_unit(metric):
    lowered = metric.lower()
    if "salary" in lowered or "amount" in lowered or "revenue" in lowered or "cost" in lowered:
        return "units as reported"
    if "percent" in lowered or "rate" in lowered or "share" in lowered:
        return "%"
    if "rating" in lowered:
        return "rating points"
    if "count" in lowered or "employees" in lowered or "headcount" in lowered:
        return "employees"
    return "records"


def _percentile(values, fraction):
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] * (upper - position) + values[upper] * (position - lower)


def requested_chart_type(question):
    text = str(question or "").lower()
    requests = (
        ("scatter", r"\bscatter(?:\s*plot)?\b"),
        ("histogram", r"\bhistograms?\b|\bdistribution\s+(?:chart|plot)\b"),
        ("box", r"\bbox\s*[- ]?plots?\b|\bboxplots?\b"),
        ("heatmap", r"\bheat\s*maps?\b|\bheatmaps?\b"),
        ("area", r"\barea\s+charts?\b|\barea\s+plots?\b"),
        ("pie", r"\bpie(?:\s+charts?)?\b|\bdoughnuts?\b|\bdonut\s+charts?\b"),
        ("line", r"\bline(?:\s+charts?)?\b|\btrend\s+charts?\b"),
        ("column", r"\bcolumn(?:\s+charts?)?\b|\bvertical\s+bars?\b"),
        ("bar", r"\bbar(?:\s+charts?)?\b|\bhorizontal\s+bars?\b"),
    )
    for chart_type, pattern in requests:
        if re.search(pattern, text):
            return chart_type
    return None


def chart_unavailable_message(chart_type, result, alternative=None):
    columns = result.get("columns", [])
    rows = result.get("rows", [])
    numeric_count = sum(
        1 for index, column in enumerate(columns)
        if not is_sensitive_column(column) and not ID_COLUMN.search(str(column).lower())
        and any(index < len(row) and isinstance(row[index], (int, float)) for row in rows)
    )
    category_count = sum(
        1 for index, column in enumerate(columns)
        if not is_sensitive_column(column) and not ID_COLUMN.search(str(column).lower())
        and any(index < len(row) and isinstance(row[index], str) for row in rows)
    )
    requirements = {
        "scatter": "a scatter plot needs two shareable numeric measures and at least three paired observations",
        "histogram": "a histogram needs at least one numeric observation in one measure",
        "box": "a box plot needs at least one numeric observation in one measure",
        "heatmap": "a heatmap needs either paired numeric measures for correlation or two categories and a numeric measure",
        "line": "a line chart needs an ordered time or date dimension and a numeric measure",
        "area": "an area chart needs an ordered time or date dimension and a numeric measure",
        "pie": "a pie chart needs a small set of non-negative category values",
        "column": "a column chart needs a category and a numeric measure",
        "bar": "a bar chart needs a category and a numeric measure",
    }
    explanation = requirements.get(chart_type, "the requested chart needs more suitable result data")
    if chart_type == "scatter" and numeric_count < 2:
        explanation += f"; these results contain only {numeric_count} shareable numeric measure(s)"
    elif chart_type == "heatmap" and category_count < 2:
        explanation += f"; these results contain only {category_count} shareable category dimension(s)"
    elif not rows and chart_type in {"histogram", "box"}:
        explanation += "; no result rows were returned"
    if alternative:
        return f"I couldn’t create the requested {chart_type} chart because {explanation}. I’ve shown a {alternative} chart instead."
    return f"I couldn’t create the requested {chart_type} chart because {explanation}. Try asking for a chart that matches the available measures and categories."


def create_business_report_pdf(report):
    output = io.BytesIO()
    green = colors.HexColor("#176344")
    deep_green = colors.HexColor("#123e2c")
    ink = colors.HexColor("#24372d")
    muted = colors.HexColor("#69786f")
    line = colors.HexColor("#e0e8e2")
    pale = colors.HexColor("#f3f7f4")
    white = colors.white
    margin = 0.72 * inch
    document = SimpleDocTemplate(
        output, pagesize=letter, leftMargin=margin, rightMargin=margin,
        topMargin=0.78 * inch, bottomMargin=0.7 * inch,
        title="Employee Insights Business Report", author="Employee Insights Assistant",
    )
    content_width = letter[0] - margin * 2
    fonts = _report_fonts()
    styles = {
        "eyebrow": ParagraphStyle("ReportEyebrow", fontName=fonts["bold"], fontSize=8, leading=11, textColor=green, spaceAfter=5),
        "title": ParagraphStyle("ReportTitle", fontName=fonts["bold"], fontSize=25, leading=29, textColor=deep_green, spaceAfter=5),
        "subtitle": ParagraphStyle("ReportSubtitle", fontName=fonts["regular"], fontSize=9, leading=13, textColor=muted),
        "section": ParagraphStyle("ReportSection", fontName=fonts["bold"], fontSize=12, leading=15, textColor=ink, spaceBefore=8, spaceAfter=7, keepWithNext=True),
        "body": ParagraphStyle("ReportBody", fontName=fonts["regular"], fontSize=9.5, leading=14, textColor=ink),
        "question": ParagraphStyle("ReportQuestion", fontName=fonts["bold"], fontSize=10, leading=15, textColor=ink),
        "summary_label": ParagraphStyle("ReportSummaryLabel", fontName=fonts["bold"], fontSize=8, leading=11, textColor=colors.HexColor("#a9d8bd"), spaceAfter=5),
        "summary": ParagraphStyle("ReportSummary", fontName=fonts["regular"], fontSize=10, leading=15, textColor=white),
        "chart_title": ParagraphStyle("ReportChartTitle", fontName=fonts["bold"], fontSize=9, leading=12, textColor=ink),
        "chart_label": ParagraphStyle("ReportChartLabel", fontName=fonts["regular"], fontSize=8, leading=10, textColor=muted),
        "chart_value": ParagraphStyle("ReportChartValue", fontName=fonts["bold"], fontSize=8, leading=10, textColor=ink, alignment=TA_RIGHT),
        "note": ParagraphStyle("ReportNote", fontName=fonts["regular"], fontSize=8, leading=11, textColor=muted),
        "bullet": ParagraphStyle("ReportBullet", fontName=fonts["regular"], fontSize=9, leading=13, textColor=ink, leftIndent=12, firstLineIndent=-9, spaceAfter=4),
        "action_number": ParagraphStyle("ReportActionNumber", fontName=fonts["bold"], fontSize=10, leading=14, textColor=green),
    }

    def paragraph(value, style):
        return Paragraph(escape(str(value or "")).replace("\n", "<br/>"), style)

    def section(title, content):
        items = content if isinstance(content, list) else [content]
        return KeepTogether([paragraph(title.upper(), styles["section"]), *items])

    class ResultChart(Flowable):
        def __init__(self, chart_data):
            super().__init__()
            self.chart_data = chart_data
            self.labels = [str(value)[:32] for value in chart_data.get("labels", [])[:12]]
            self.values = [float(value) for value in chart_data.get("values", [])[:12]]
            self.chart_type = chart_data.get("type", "bar")
            self.height = 190 if self.chart_type in {"pie", "donut"} else 230 if self.chart_type == "heatmap" else 205 if self.chart_type in {"scatter", "box", "line", "area", "column", "histogram"} else 24 * len(self.labels) + 12

        def wrap(self, available_width, available_height):
            self.width = available_width
            return self.width, self.height

        def draw(self):
            canvas = self.canv
            plot_left = 76
            plot_right = self.width - 12
            plot_bottom = 28
            plot_top = self.height - 17
            count = len(self.values)
            maximum = max([0.0] + self.values) or 1.0
            minimum = min([0.0] + self.values)
            palette = [colors.HexColor(value) for value in CHART_PALETTE]
            canvas.setFont(fonts["regular"], 7)
            canvas.setFillColor(muted)

            if self.chart_type in {"pie", "donut"}:
                if not count:
                    return
                total = sum(max(0, value) for value in self.values)
                if total <= 0:
                    return
                center_x = min(110, self.width * 0.28)
                center_y = self.height / 2
                outer_radius = min(61, self.height * 0.35)
                inner_radius = outer_radius * 0.57 if self.chart_type == "donut" else 0
                angle = 0.0
                for index, value in enumerate(self.values):
                    sweep = max(0, value) / total * math.tau
                    if sweep <= 0:
                        continue
                    steps = max(2, int(sweep * 24))
                    points = [(center_x, center_y)] if not inner_radius else []
                    for step in range(steps + 1):
                        theta = angle + sweep * step / steps - math.pi / 2
                        points.append((center_x + outer_radius * math.cos(theta), center_y + outer_radius * math.sin(theta)))
                    if inner_radius:
                        for step in range(steps, -1, -1):
                            theta = angle + sweep * step / steps - math.pi / 2
                            points.append((center_x + inner_radius * math.cos(theta), center_y + inner_radius * math.sin(theta)))
                    path = canvas.beginPath()
                    path.moveTo(*points[0])
                    for point in points[1:]:
                        path.lineTo(*point)
                    path.close()
                    canvas.setFillColor(palette[index % len(palette)])
                    canvas.drawPath(path, fill=1, stroke=0)
                    angle += sweep
                legend_x = center_x + outer_radius + 20
                legend_y = self.height - 30
                canvas.setFont(fonts["regular"], 8)
                for index, (label, value) in enumerate(zip(self.labels, self.values)):
                    y = legend_y - index * 21
                    if y < 12:
                        break
                    canvas.setFillColor(palette[index % len(palette)])
                    canvas.circle(legend_x, y + 3, 4, fill=1, stroke=0)
                    canvas.setFillColor(ink)
                    canvas.drawString(legend_x + 10, y, label[:24])
                    canvas.setFillColor(muted)
                    share = value / total * 100
                    canvas.drawRightString(self.width - 8, y, f"{value:,.2f}".rstrip("0").rstrip(".") + f"  ({share:.0f}%)")
                return

            if self.chart_type == "scatter":
                points = self.chart_data.get("points", [])
                points = [point for point in points if isinstance(point, dict) and isinstance(point.get("x"), (int, float)) and isinstance(point.get("y"), (int, float))][:300]
                if len(points) < 3:
                    return
                x_values = [float(point["x"]) for point in points]
                y_values = [float(point["y"]) for point in points]
                x_min, x_max = min(x_values), max(x_values)
                y_min, y_max = min(y_values), max(y_values)
                x_range, y_range = max(x_max - x_min, 1), max(y_max - y_min, 1)
                left, right, bottom, top = 48, self.width - 8, 30, self.height - 16
                canvas.setStrokeColor(line)
                for tick in range(5):
                    x = left + (right - left) * tick / 4
                    y = bottom + (top - bottom) * tick / 4
                    canvas.line(x, bottom, x, top)
                    canvas.line(left, y, right, y)
                    canvas.setFillColor(muted)
                    canvas.drawCentredString(x, bottom - 12, f"{x_min + (x_max - x_min) * tick / 4:,.3g}")
                    canvas.drawRightString(left - 4, y - 2, f"{y_min + (y_max - y_min) * tick / 4:,.3g}")
                canvas.setFillColor(green)
                for x_value, y_value in zip(x_values, y_values):
                    x = left + (x_value - x_min) / x_range * (right - left)
                    y = bottom + (y_value - y_min) / y_range * (top - bottom)
                    canvas.circle(x, y, 2.4, fill=1, stroke=0)
                canvas.setFillColor(muted)
                canvas.drawCentredString((left + right) / 2, 7, self.chart_data.get("x_label", "X")[:45])
                canvas.drawString(0, self.height - 8, self.chart_data.get("y_label", "Y")[:45])
                return

            if self.chart_type == "box":
                groups = self.chart_data.get("groups", [])[:8]
                values = [float(group[key]) for group in groups for key in ("min", "max") if isinstance(group.get(key), (int, float))]
                if not groups or not values:
                    return
                data_min, data_max = min(values), max(values)
                value_range = max(data_max - data_min, 1)
                left, right = min(130, self.width * 0.3), self.width - 10
                row_height = min(22, (self.height - 25) / len(groups))
                for index, group in enumerate(groups):
                    y = self.height - 16 - (index + 0.5) * row_height
                    x = lambda value: left + (float(value) - data_min) / value_range * (right - left)
                    canvas.setFillColor(muted)
                    canvas.drawRightString(left - 6, y - 2, str(group.get("label", "Group"))[:20])
                    canvas.setStrokeColor(green)
                    canvas.setLineWidth(1)
                    canvas.line(x(group["min"]), y, x(group["max"]), y)
                    canvas.setFillColor(colors.HexColor("#b0c9b7"))
                    canvas.rect(x(group["q1"]), y - 5, max(1, x(group["q3"]) - x(group["q1"])), 10, fill=1, stroke=1)
                    canvas.setStrokeColor(deep_green)
                    canvas.line(x(group["median"]), y - 6, x(group["median"]), y + 6)
                canvas.setFillColor(muted)
                for tick in range(5):
                    tick_value = data_min + value_range * tick / 4
                    canvas.drawCentredString(left + (right - left) * tick / 4, 12, f"{tick_value:,.3g}")
                canvas.drawCentredString((left + right) / 2, 5, self.chart_data.get("x_label", "Measure")[:45])
                return

            if self.chart_type == "heatmap":
                x_labels = self.chart_data.get("x_labels", [])[:10]
                y_labels = self.chart_data.get("y_labels", [])[:10]
                matrix = self.chart_data.get("matrix", [])[:10]
                values = [float(value) for row in matrix for value in row if isinstance(value, (int, float))]
                if len(x_labels) < 2 or len(y_labels) < 2 or not values:
                    return
                data_min, data_max = min(values), max(values)
                value_range = max(data_max - data_min, 1)
                left = min(105, self.width * 0.23)
                top = self.height - 26
                cell_width = (self.width - left - 8) / len(x_labels)
                cell_height = (self.height - 65) / len(y_labels)
                for row_index, label in enumerate(y_labels):
                    y = top - (row_index + 1) * cell_height
                    canvas.setFillColor(muted)
                    canvas.drawRightString(left - 5, y + cell_height / 2 - 2, str(label)[:16])
                    for column_index, x_label in enumerate(x_labels):
                        value = float(matrix[row_index][column_index])
                        intensity = max(0, min(1, (value - data_min) / value_range))
                        red = 0.92 - 0.48 * intensity
                        green_channel = 0.96 - 0.35 * intensity
                        blue = 0.93 - 0.45 * intensity
                        canvas.setFillColor(colors.Color(red, green_channel, blue))
                        canvas.rect(left + column_index * cell_width, y, cell_width - 1, cell_height - 1, fill=1, stroke=0)
                        if len(x_labels) <= 7 and len(y_labels) <= 7:
                            canvas.setFillColor(ink)
                            canvas.drawCentredString(left + (column_index + 0.5) * cell_width, y + cell_height / 2 - 2, f"{value:,.1f}")
                canvas.setFillColor(muted)
                for index, label in enumerate(x_labels):
                    canvas.drawCentredString(left + (index + 0.5) * cell_width, 3, str(label)[:12])
                legend_left = left + 45
                legend_width = min(120, self.width - legend_left - 8)
                for index in range(8):
                    intensity = index / 7
                    color = colors.Color(0.92 - 0.48 * intensity, 0.96 - 0.35 * intensity, 0.93 - 0.45 * intensity)
                    canvas.setFillColor(color)
                    canvas.rect(legend_left + legend_width * index / 8, 17, legend_width / 8, 7, fill=1, stroke=0)
                canvas.setFillColor(muted)
                canvas.drawString(legend_left, 8, f"{data_min:,.3g}")
                canvas.drawRightString(legend_left + legend_width, 8, f"{data_max:,.3g}")
                return

            if self.chart_type == "bar":
                if not count:
                    return
                row_height = self.height / max(count, 1)
                label_x = 0
                bar_left = min(145, self.width * 0.33)
                bar_width = self.width - bar_left - 57
                for index, (label, value) in enumerate(zip(self.labels, self.values)):
                    y = self.height - (index + 1) * row_height + row_height / 2
                    canvas.setFillColor(muted)
                    canvas.setFont(fonts["regular"], 7)
                    canvas.drawString(label_x, y - 2, label[:22])
                    canvas.setFillColor(colors.HexColor("#e8efe9"))
                    canvas.roundRect(bar_left, y - 3, bar_width, 7, 3.5, fill=1, stroke=0)
                    canvas.setFillColor(green)
                    canvas.roundRect(bar_left, y - 3, max(2, bar_width * max(0, value) / maximum), 7, 3.5, fill=1, stroke=0)
                    canvas.setFillColor(ink)
                    canvas.drawRightString(self.width, y - 2, f"{value:,.2f}".rstrip("0").rstrip("."))
                return

            plot_width = plot_right - plot_left
            plot_height = plot_top - plot_bottom
            value_range = max(maximum - minimum, 1)
            for tick in range(5):
                y = plot_bottom + plot_height * tick / 4
                canvas.setStrokeColor(line)
                canvas.setLineWidth(0.5)
                canvas.line(plot_left, y, plot_right, y)
            points = []
            for index, value in enumerate(self.values):
                x = plot_left + (plot_width / 2 if count == 1 else plot_width * index / (count - 1))
                y = plot_bottom + (value - minimum) / value_range * plot_height
                points.append((x, y))
            if self.chart_type in {"line", "area"}:
                path = canvas.beginPath()
                path.moveTo(*points[0])
                for point in points[1:]:
                    path.lineTo(*point)
                if self.chart_type == "area":
                    zero_y = plot_bottom + (0 - minimum) / value_range * plot_height
                    area_path = canvas.beginPath()
                    area_path.moveTo(points[0][0], zero_y)
                    area_path.lineTo(*points[0])
                    for point in points[1:]:
                        area_path.lineTo(*point)
                    area_path.lineTo(points[-1][0], zero_y)
                    area_path.close()
                    canvas.setFillColor(colors.HexColor("#d5e1d8"))
                    canvas.drawPath(area_path, stroke=0, fill=1)
                canvas.setStrokeColor(green)
                canvas.setLineWidth(2)
                canvas.drawPath(path, stroke=1, fill=0)
                for index, (x, y) in enumerate(points):
                    canvas.setFillColor(palette[index % len(palette)])
                    canvas.circle(x, y, 3.5, fill=1, stroke=0)
                    canvas.setFillColor(ink)
                    canvas.setFont(fonts["regular"], 7)
                    canvas.drawCentredString(x, y + 7, f"{self.values[index]:,.2f}".rstrip("0").rstrip("."))
            else:
                slot_width = plot_width / count
                bar_width = slot_width * 0.58
                zero_y = plot_bottom + (0 - minimum) / value_range * plot_height
                for index, (x, value) in enumerate(points):
                    height = max(1, abs(value - zero_y))
                    canvas.setFillColor(palette[index % len(palette)])
                    canvas.roundRect(x - bar_width / 2, min(value, zero_y), bar_width, height, 3, fill=1, stroke=0)
                    canvas.setFillColor(ink)
                    canvas.setFont(fonts["regular"], 7)
                    canvas.drawCentredString(x, max(value, zero_y) + 4, f"{self.values[index]:,.2f}".rstrip("0").rstrip("."))
            canvas.setFillColor(muted)
            canvas.setFont(fonts["regular"], 7)
            for index, (x, _) in enumerate(points):
                canvas.drawCentredString(x, 10, self.labels[index][:12])

    def draw_page(canvas, doc):
        canvas.saveState()
        page_width, page_height = letter
        canvas.setFillColor(green)
        canvas.roundRect(margin, page_height - 39, 19, 19, 5, fill=1, stroke=0)
        canvas.setFillColor(white)
        canvas.setFont(fonts["bold"], 10)
        canvas.drawCentredString(margin + 9.5, page_height - 33, "E")
        canvas.setFillColor(ink)
        canvas.setFont(fonts["bold"], 8)
        canvas.drawString(margin + 27, page_height - 31, "EMPLOYEE INSIGHTS")
        canvas.setFillColor(muted)
        canvas.setFont(fonts["regular"], 7)
        canvas.drawRightString(page_width - margin, page_height - 31, "BUSINESS REPORT")
        canvas.setStrokeColor(line)
        canvas.setLineWidth(0.7)
        canvas.line(margin, page_height - 48, page_width - margin, page_height - 48)
        canvas.line(margin, 39, page_width - margin, 39)
        canvas.setFillColor(muted)
        canvas.setFont(fonts["regular"], 7)
        canvas.drawString(margin, 26, "© 2026. All rights reserved.")
        canvas.drawCentredString(page_width / 2, 26, f"{doc.page}")
        canvas.drawRightString(page_width - margin, 26, "Created by Mary Prathyusha Basani")
        canvas.restoreState()

    story = [
        paragraph("WORKFORCE ANALYSIS  /  BUSINESS REPORT", styles["eyebrow"]),
        paragraph(report.get("title", "Employee Workforce Business Report"), styles["title"]),
        paragraph(f"Prepared {datetime.now().strftime('%B %d, %Y')}  ·  Workforce insights", styles["subtitle"]),
        Spacer(1, 16),
    ]
    question = Table([
        [paragraph("BUSINESS QUESTION", styles["eyebrow"])],
        [paragraph(report.get("question", ""), styles["question"])],
    ], colWidths=[content_width])
    question.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), pale), ("BOX", (0, 0), (-1, -1), 0.6, line),
        ("LINEBEFORE", (0, 0), (0, -1), 3, green), ("LEFTPADDING", (0, 0), (-1, -1), 13),
        ("RIGHTPADDING", (0, 0), (-1, -1), 13), ("TOPPADDING", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2), ("TOPPADDING", (0, 1), (-1, 1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 11),
    ]))
    story.extend([question, Spacer(1, 14)])
    summary = Table([
        [paragraph("EXECUTIVE SUMMARY", styles["summary_label"])],
        [paragraph(report.get("answer", "No summary was returned for this analysis."), styles["summary"])],
    ], colWidths=[content_width])
    summary.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), deep_green), ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14), ("TOPPADDING", (0, 0), (-1, 0), 12),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1), ("TOPPADDING", (0, 1), (-1, 1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 13),
    ]))
    story.extend([summary, Spacer(1, 10)])

    objective_scope = Table([
        [paragraph("OBJECTIVE", styles["eyebrow"]), paragraph("SCOPE", styles["eyebrow"])],
        [paragraph(report.get("business_objective", report.get("question", "")), styles["body"]),
         paragraph(report.get("scope", "Analysis covers the result set returned for this question."), styles["body"])],
    ], colWidths=[content_width * 0.5, content_width * 0.5])
    objective_scope.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), pale), ("BOX", (0, 0), (-1, -1), 0.5, line),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, line), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([section("Business objective and scope", objective_scope), Spacer(1, 4)])

    methodology = [
        paragraph(f"Data source: {report.get('data_source', 'Employee workforce records relevant to the requested analysis.')}", styles["body"]),
        paragraph(f"Cleaning and methodology: {report.get('methodology', 'A read-only query was analyzed using a normalized in-memory copy. The source database was not changed.')}", styles["body"]),
    ]
    story.extend([section("Data sources, cleaning, and methodology", methodology), Spacer(1, 4)])

    chart = report.get("chart") or {}
    if chart.get("type"):
        x_label = chart.get("x_label")
        y_label = chart.get("y_label")
        x_unit = chart.get("x_unit")
        y_unit = chart.get("y_unit")
        chart_context = " · ".join(value for value in [
            f"X: {x_label}{f' ({x_unit})' if x_unit else ''}" if x_label else None,
            f"Y: {y_label}{f' ({y_unit})' if y_unit else ''}" if y_label else None,
            f"Units: {chart.get('unit')}" if chart.get("unit") and not y_unit else None,
        ] if value)
        chart_group = [paragraph(chart.get("title", "Results"), styles["chart_title"])]
        if chart_context:
            chart_group.append(paragraph(chart_context, styles["chart_label"]))
        chart_group.extend([Spacer(1, 5), ResultChart(chart)])
        story.extend([section("Key findings, charts, and supporting figures", chart_group), Spacer(1, 4)])
    else:
        story.extend([section(
            "Key findings, charts, and supporting figures",
            paragraph("No chart could be prepared from the available result shape. See the verified findings and chart-availability note for context.", styles["body"]),
        ), Spacer(1, 4)])

    for table_data in report.get("tables", []):
        headers = table_data.get("columns", [])
        rows = table_data.get("rows", [])
        if not headers or not rows:
            continue
        table_cells = [[paragraph(value, styles["chart_title"]) for value in headers]]
        table_cells.extend([
            [paragraph(value, styles["note"]) for value in row]
            for row in rows
        ])
        column_width = content_width / len(headers)
        result_table = Table(
            table_cells,
            colWidths=[column_width] * len(headers),
            repeatRows=1,
            hAlign="LEFT",
        )
        result_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), deep_green),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, pale]),
            ("GRID", (0, 0), (-1, -1), 0.35, line),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.extend([
            paragraph(table_data.get("title", "Supporting data"), styles["chart_title"]),
            result_table,
            Spacer(1, 8),
        ])

    insights = report.get("insights") or []
    if insights:
        finding_list = [paragraph(f"•  {item}", styles["bullet"]) for item in insights[:3]]
        story.extend([section("Key findings: verified", finding_list), Spacer(1, 2)])
    else:
        story.extend([section(
            "Key findings: verified",
            paragraph("No additional verified finding was returned beyond the executive summary.", styles["body"]),
        ), Spacer(1, 2)])

    recommendations = report.get("recommendations") or []
    possible_explanations = report.get("possible_explanations") or []
    if possible_explanations:
        explanation_list = [paragraph(f"•  {item if str(item).lower().startswith('possible explanation:') else f'Possible explanation: {item}' }", styles["bullet"]) for item in possible_explanations[:2]]
        story.extend([section("Possible explanations: not verified", explanation_list), Spacer(1, 2)])
    else:
        story.extend([section(
            "Possible explanations: not verified",
            paragraph("No causal explanation is asserted from these results.", styles["body"]),
        ), Spacer(1, 2)])

    chart_notice = report.get("chart_notice")
    if chart_notice:
        story.extend([section("Chart availability", paragraph(chart_notice, styles["note"])), Spacer(1, 2)])

    implications = report.get("implications") or []
    if implications:
        implication_list = [paragraph(f"•  {item}", styles["bullet"]) for item in implications[:2]]
        story.extend([section("Business implications", implication_list), Spacer(1, 2)])
    else:
        story.extend([section(
            "Business implications",
            paragraph("No broader business implication can be established from the available result fields alone.", styles["body"]),
        ), Spacer(1, 2)])

    if recommendations:
        actions = []
        for index, recommendation in enumerate(recommendations[:3], start=1):
            actions.append([
                paragraph(f"{index:02d}", styles["action_number"]),
                paragraph(recommendation, styles["body"]),
            ])
        action_table = Table(actions, colWidths=[0.45 * inch, content_width - 0.45 * inch])
        action_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), pale), ("BOX", (0, 0), (-1, -1), 0.5, line),
            ("LINEBEFORE", (0, 0), (0, -1), 2, green), ("LINEBELOW", (0, 0), (-1, -2), 0.5, line),
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.extend([section("Recommended next steps", action_table), Spacer(1, 4)])
    else:
        story.extend([section(
            "Recommended next steps",
            paragraph("Validate result coverage and compare any material finding with an approved business target before taking action.", styles["body"]),
        ), Spacer(1, 4)])

    limitations = report.get("limitations") or []
    limitation_items = [str(item) for item in limitations if isinstance(item, str) and item.strip()]
    if report.get("truncated"):
        limitation_items.append("The returned result was limited and may not represent the full workforce.")
    if not limitation_items:
        limitation_items.append("Findings describe only the fields and records returned for this question; causal explanations and unqueried business targets were not independently validated.")
    conclusion = report.get("conclusion") or report.get("answer", "No conclusion was returned for this analysis.")
    limitations_and_conclusion = [
        paragraph(f"Limitations: {' '.join(limitation_items[:3])}", styles["body"]),
        paragraph(f"Conclusion: {conclusion}", styles["body"]),
    ]
    story.extend([section("Data limitations and conclusion", limitations_and_conclusion)])

    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return output.getvalue()


def send_report_email(pdf_bytes, recipient, question):
    host = os.getenv("SMTP_HOST", "").strip()
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM_EMAIL", "").strip()
    if not all((host, username, password, sender)):
        return "not_configured"
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
        use_ssl = os.getenv("SMTP_USE_SSL", "false").strip().lower() in {"1", "true", "yes"}
        use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() in {"1", "true", "yes"}
        message = EmailMessage()
        message["Subject"] = "Your Employee Insights business report"
        message["From"] = sender
        message["To"] = recipient
        message.set_content("Your requested workforce analysis report is attached. Findings are based on the returned query results.")
        message.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename="employee-insights-business-report.pdf")
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context()) as server:
                server.login(username, password)
                refused = server.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=20) as server:
                if use_tls:
                    server.starttls(context=ssl.create_default_context())
                server.login(username, password)
                refused = server.send_message(message)
        return "failed" if refused else "sent"
    except Exception as error:
        smtp_code = getattr(error, "smtp_code", None)
        response = f", SMTP response {smtp_code}" if isinstance(smtp_code, int) else ""
        logging.getLogger(__name__).error(
            "Report email delivery failed (%s%s)", type(error).__name__, response
        )
        return "failed"


def valid_email_address(value):
    return isinstance(value, str) and len(value) <= 254 and bool(EMAIL_PATTERN.fullmatch(value.strip()))
