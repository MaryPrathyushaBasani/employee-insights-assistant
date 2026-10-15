import os
import unittest
from copy import deepcopy
from unittest.mock import MagicMock, patch

import app as assistant_app
from business_reports import (
    _report_fonts,
    build_chart,
    build_employee_analysis,
    build_employee_chart,
    build_employee_dataset,
    chart_unavailable_message,
    clean_query_result,
    create_business_report_pdf,
    requested_chart_type,
    safe_analysis_rows,
    send_report_email,
)


class BusinessReportTests(unittest.TestCase):
    def setUp(self):
        assistant_app.app.config["TESTING"] = True
        assistant_app._reports.clear()
        self.client = assistant_app.app.test_client()

    def sample_result(self):
        return {
            "kind": "rows",
            "columns": ["department", "headcount", "employee email"],
            "rows": [
                ["  hr ", "12", "one@example.com"],
                ["HR", "12", "one@example.com"],
                ["Finance", "8", "two@example.com"],
            ],
            "row_count": 3,
            "truncated": False,
        }

    def employee_result(self):
        return {
            "kind": "rows",
            "columns": ["emp_id", "name", "department", "salary", "join_date", "city", "email", "phone", "rating", "status"],
            "rows": [
                [1, "One", "HR", 100, "2025-01-01", "A", "one@example.com", "", 4, "Active"],
                [2, "Two", "hr", -50, "bad-date", "A", "two@example.com", "", 0, "Active"],
                [3, "Three", "H.R.", 0, "", "B", "three@example.com", "", 5, "Active"],
                [4, "Four", "PM", "bad", "not-a-date", "B", "four@example.com", "", 7, "Active"],
                [5, "Five", "Product", 200, "2025-02-15", "C", "five@example.com", "", 3, "Active"],
                [5, "Five", "Product", 200, "2025-02-15", "C", "five@example.com", "", 3, "Active"],
                [6, "Six", "Finance", 300, "2025-03-15", "C", "six@example.com", "", 2, "Active"],
                [6, "Six", "Finance", 400, "2025-04-15", "C", "six@example.com", "", 4, "Active"],
            ],
            "row_count": 8,
            "truncated": False,
        }

    def test_cleaner_normalizes_copy_without_dropping_distinct_source_rows(self):
        source = self.sample_result()
        original = deepcopy(source)
        cleaned, quality = clean_query_result(source)

        self.assertEqual(source, original)
        self.assertEqual(cleaned["rows"], [
            ["HR", 12, "one@example.com"],
            ["HR", 12, "one@example.com"],
            ["Finance", 8, "two@example.com"],
        ])
        self.assertEqual(quality["duplicate_rows"], 0)
        self.assertEqual(quality["missing_values"], 0)
        self.assertEqual(quality["converted_types"], 3)
        self.assertTrue(quality["source_unchanged"])
        self.assertEqual(safe_analysis_rows(cleaned)[0], ["department", "headcount"])

    def test_cleaner_counts_exact_duplicates_before_normalization_and_keeps_them(self):
        source = {
            "columns": ["department", "headcount"],
            "rows": [["Finance", "8"], ["Finance", "8"], ["finance", "8"]],
        }

        cleaned, quality = clean_query_result(source)

        self.assertEqual(quality["duplicate_rows"], 1)
        self.assertEqual(cleaned["row_count"], 3)
        self.assertEqual(cleaned["rows"], [["Finance", 8], ["Finance", 8], ["Finance", 8]])

    def test_invalid_numeric_and_date_values_are_counted(self):
        source = {
            "columns": ["total_count", "hire_date"],
            "rows": [["bad", "not-a-date"], ["2", "2024-01-02"]],
        }

        cleaned, quality = clean_query_result(source)

        self.assertEqual(cleaned["rows"], [[None, None], [2, "2024-01-02"]])
        self.assertEqual(quality["missing_values"], 2)
        self.assertEqual(quality["converted_types"], 3)

    def test_chart_uses_aggregate_values_and_excludes_contact_fields(self):
        cleaned, _ = clean_query_result(self.sample_result())
        chart = build_chart(cleaned)

        self.assertEqual(chart["type"], "donut")
        self.assertEqual(chart["labels"], ["HR", "Finance"])
        self.assertEqual(chart["values"], [24.0, 8.0])
        self.assertNotIn("one@example.com", str(chart))

    def test_chart_selects_line_for_time_series_results(self):
        result = {
            "columns": ["join_date", "employee_count"],
            "rows": [["2025-01-01", 8], ["2025-02-01", 11], ["2025-03-01", 9]],
        }

        chart = build_chart(result)

        self.assertEqual(chart["type"], "line")
        self.assertEqual(chart["labels"], ["2025-01-01", "2025-02-01", "2025-03-01"])
        self.assertEqual(chart["values"], [8.0, 11.0, 9.0])

    def test_requested_chart_types_use_result_shape(self):
        grouped = {
            "columns": ["department", "headcount"],
            "rows": [["HR", 12], ["Finance", 8], ["Support", 6]],
        }
        dates = {
            "columns": ["join_date", "employee_count"],
            "rows": [["2025-01-01", 8], ["2025-02-01", 11], ["2025-03-01", 9]],
        }
        numeric_pairs = {
            "columns": ["average_salary", "rating"],
            "rows": [[50000, 3.1], [60000, 3.4], [70000, 3.8], [80000, 4.1]],
        }
        numeric_only = {
            "columns": ["salary"],
            "rows": [[50000], [60000], [70000], [80000], [90000], [100000]],
        }
        grouped_distribution = {
            "columns": ["department", "salary"],
            "rows": [[department, value] for department, values in {
                "HR": [50, 55, 60, 65, 70], "Finance": [60, 70, 80, 90, 100],
            }.items() for value in values],
        }
        matrix = {
            "columns": ["city", "department", "headcount"],
            "rows": [[city, department, value] for city, department, value in [
                ("Chennai", "HR", 3), ("Chennai", "Finance", 4),
                ("Delhi", "HR", 5), ("Delhi", "Finance", 6),
            ]],
        }

        self.assertEqual(build_chart(grouped, "pie")["type"], "pie")
        self.assertEqual(build_chart(dates, "line")["type"], "line")
        self.assertEqual(build_chart(dates, "area")["type"], "area")
        self.assertEqual(build_chart(numeric_pairs, "scatter")["type"], "scatter")
        self.assertEqual(build_chart(numeric_pairs)["type"], "scatter")
        self.assertEqual(build_chart(numeric_only, "histogram")["type"], "histogram")
        self.assertEqual(build_chart(grouped_distribution, "box")["type"], "box")
        self.assertEqual(build_chart(matrix, "heatmap")["type"], "heatmap")
        self.assertEqual(build_chart(grouped, "bar")["type"], "bar")
        self.assertEqual(build_chart(grouped, "column")["type"], "column")

    def test_histogram_and_box_plot_support_small_samples_and_outliers(self):
        histogram = build_chart({"columns": ["salary"], "rows": [[125]]}, "histogram")
        box = build_chart({
            "columns": ["department", "salary"],
            "rows": [["A", value] for value in (1, 2, 3, 4, 5, 100)],
        }, "box")

        self.assertEqual(histogram["values"], [1])
        self.assertEqual(box["groups"][0]["max"], 5)
        self.assertEqual(box["groups"][0]["outliers"], [100])

    def test_execute_query_preserves_mysql_date_format_percent_tokens(self):
        query = "SELECT DATE_FORMAT('2025-01-02', '%Y-%m') AS month"
        engine = MagicMock()
        connection = MagicMock()
        transaction = MagicMock()
        transaction.__enter__.return_value = connection
        engine.begin.return_value = transaction
        result = MagicMock()
        result.returns_rows = False
        result.rowcount = 0
        connection.execute.return_value = result

        with patch.object(assistant_app, "get_engine", return_value=engine):
            response = assistant_app.execute_query(query, select_only=True)

        self.assertEqual(response["affected_rows"], 0)
        statement = connection.execute.call_args.args[0]
        self.assertEqual(statement.text, query)
        self.assertIn("%Y-%m", statement.text)

    def test_unavailable_chart_explains_data_requirements_and_alternative(self):
        result = {
            "columns": ["department", "headcount"],
            "rows": [["HR", 12], ["Finance", 8], ["Support", 6]],
        }

        self.assertEqual(requested_chart_type("Show a pie of headcount"), "pie")
        self.assertEqual(requested_chart_type("Plot headcount as a heatmap"), "heatmap")
        self.assertIsNone(build_chart(result, "scatter"))
        notice = chart_unavailable_message("scatter", result, "column")
        self.assertIn("two shareable numeric measures", notice)
        self.assertIn("column chart instead", notice)

    def test_chart_selects_columns_for_short_comparisons_and_bars_for_long_lists(self):
        short_result = {
            "columns": ["department", "average_salary"],
            "rows": [["HR", 75000], ["Finance", 81000], ["Support", 68000]],
        }
        long_result = {
            "columns": ["city", "average_salary"],
            "rows": [[f"City {index}", 50000 + index * 1000] for index in range(8)],
        }

        self.assertEqual(build_chart(short_result)["type"], "column")
        self.assertEqual(build_chart(long_result)["type"], "bar")

    def test_status_codes_are_not_exposed_in_analysis_or_chart(self):
        result = {"columns": ["status", "employee_count"], "rows": [["A", 12], ["I", 5], ["T", 2]]}

        columns, rows = safe_analysis_rows(result)
        chart = build_chart(result)

        self.assertEqual(rows, [["Active", 12], ["Other status category", 5], ["Other status category", 2]])
        self.assertNotIn('"I"', str(chart))
        self.assertEqual(chart["labels"], ["Active", "Other status category"])
        self.assertEqual(chart["values"], [12.0, 7.0])

    def test_analysis_supplies_a_grounded_recommendation_when_model_omits_one(self):
        with patch.object(
            assistant_app,
            "azure_chat_completion",
            return_value='{"answer":"HR has 12 employees.","insights":[],"recommendations":[]}',
        ):
            analysis = assistant_app.analyze_result(
                "Review department headcount",
                {"columns": ["department", "headcount"], "rows": [["HR", 12]]},
            )

        self.assertEqual(len(analysis["recommendations"]), 1)
        self.assertIn("business target", analysis["recommendations"][0])

    def test_pdf_is_generated_with_findings_chart_and_quality(self):
        cleaned, quality = clean_query_result(self.sample_result())
        report = {
            "question": "Compare headcount by department",
            "answer": "HR has the larger count in these results.",
            "insights": ["HR exceeds Finance by four employees."],
            "recommendations": ["Review team capacity against current priorities."],
            "chart": build_chart(cleaned),
            "truncated": quality["truncated"],
        }

        pdf = create_business_report_pdf(report)

        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 1000)

    def test_pdf_embeds_times_new_roman(self):
        pdf = create_business_report_pdf({"question": "Review headcount", "answer": "The result is ready."})

        self.assertEqual(_report_fonts()["regular"], "TimesNewRoman")
        self.assertIn(b"TimesNewRoman", pdf)

    def test_pdf_renders_each_supported_chart_type(self):
        charts = {
            chart_type: {
                "type": chart_type,
                "title": "Results by group",
                "labels": ["Group A", "Group B", "Group C"],
                "values": [12, 8, 5],
                "unit": "employees",
                "x_label": "Group",
                "y_label": "Headcount",
            }
            for chart_type in ("bar", "column", "line", "area", "donut", "pie", "histogram")
        }
        charts["scatter"] = {
            "type": "scatter", "title": "Salary vs rating", "labels": [], "values": [],
            "points": [{"x": 50, "y": 3.1}, {"x": 60, "y": 3.4}, {"x": 70, "y": 3.8}],
            "x_label": "Salary", "y_label": "Rating",
        }
        charts["box"] = {
            "type": "box", "title": "Salary distribution", "labels": [], "values": [],
            "groups": [{"label": "HR", "min": 50, "q1": 55, "median": 60, "q3": 65, "max": 70}],
            "x_label": "Group", "y_label": "Salary",
        }
        charts["heatmap"] = {
            "type": "heatmap", "title": "Headcount by city and department", "labels": [], "values": [],
            "x_labels": ["HR", "Finance"], "y_labels": ["Chennai", "Delhi"],
            "matrix": [[3, 4], [5, 6]], "x_label": "Department", "y_label": "City",
        }
        for chart_type, chart in charts.items():
            with self.subTest(chart_type=chart_type):
                pdf = create_business_report_pdf({
                    "question": "Review workforce results",
                    "answer": "The returned results show a measurable difference.",
                    "insights": ["The result varies across the compared groups."],
                    "recommendations": ["Review the largest variance with the relevant team."],
                    "chart": chart,
                })

                self.assertTrue(pdf.startswith(b"%PDF-"))
                self.assertGreater(len(pdf), 1000)

    def test_requirement_creates_report_and_auto_emails_with_read_only_query(self):
        answer = {
            "answer": "Chennai has 12 employees and Delhi has 8.",
            "insights": ["Chennai exceeds Delhi by four employees."],
            "recommendations": ["Review team capacity against current priorities."],
        }
        city_result = {
            "kind": "rows",
            "columns": ["city", "headcount", "employee email"],
            "rows": [["Chennai", "12", "one@example.com"], ["Delhi", "8", "two@example.com"]],
            "row_count": 2,
            "truncated": False,
        }
        with (
            patch.object(assistant_app, "lookup_phone_owner", return_value=None),
            patch.object(assistant_app, "generate_sql", return_value={"action": "query", "sql": "SELECT city, headcount FROM employees"}),
            patch.object(assistant_app, "execute_query", return_value=city_result) as execute_query,
            patch.object(assistant_app, "analyze_result", return_value=answer),
            patch.object(assistant_app, "send_report_email", return_value="sent") as send_email,
        ):
            response = self.client.post("/api/requirement", json={
                "requirement": "Compare headcount by city",
                "auto_email": True,
                "email": "mary@example.com",
            })

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["email_status"], "sent")
        self.assertIsNotNone(payload["analysis"]["chart"])
        self.assertNotIn("data_quality", payload["analysis"])
        self.assertTrue(payload["report_url"].endswith(".pdf"))
        execute_query.assert_called_once_with("SELECT city, headcount FROM employees", select_only=True)
        send_email.assert_called_once()
        pdf_response = self.client.get(payload["report_url"])
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response.mimetype, "application/pdf")
        self.assertTrue(pdf_response.data.startswith(b"%PDF-"))

    def test_requirement_explains_unavailable_chart_without_substitution(self):
        answer = {
            "answer": "The results compare headcount by city.",
            "insights": [],
            "possible_explanations": [],
            "implications": [],
            "recommendations": [],
            "conclusion": "The returned cities differ in headcount.",
        }
        city_result = {
            "kind": "rows",
            "columns": ["city", "headcount"],
            "rows": [["Chennai", 12], ["Delhi", 8], ["Mumbai", 6]],
            "row_count": 3,
            "truncated": False,
        }
        with (
            patch.object(assistant_app, "lookup_phone_owner", return_value=None),
            patch.object(assistant_app, "generate_sql", return_value={"action": "query", "sql": "SELECT city, headcount FROM employees"}),
            patch.object(assistant_app, "execute_query", return_value=city_result),
            patch.object(assistant_app, "analyze_result", return_value=answer),
        ):
            response = self.client.post("/api/requirement", json={
                "requirement": "Show a scatter plot of headcount by city",
            })

        self.assertEqual(response.status_code, 200)
        analysis = response.get_json()["analysis"]
        self.assertIsNone(analysis["chart"])
        self.assertIn("scatter plot needs two", analysis["chart_notice"])
        self.assertNotIn("instead", analysis["chart_notice"])

    def test_employee_dataset_counts_duplicates_valid_values_and_department_reviews(self):
        source = self.employee_result()
        original = deepcopy(source)

        dataset = build_employee_dataset(source)
        quality = dataset["quality"]

        self.assertEqual(source, original)
        self.assertEqual(quality["source_rows"], 8)
        self.assertEqual(quality["exact_duplicate_rows"], 1)
        self.assertEqual(quality["distinct_employee_ids"], 6)
        self.assertEqual(quality["duplicate_employee_id_count"], 1)
        self.assertEqual(quality["duplicate_employee_extra_rows"], 1)
        self.assertEqual(quality["employee_rows_used_for_metrics"], 5)
        self.assertEqual(quality["valid_salary_observations"], 2)
        self.assertEqual(quality["valid_rating_observations"], 3)
        self.assertEqual(quality["valid_salary_rating_pairs"], 2)
        self.assertEqual(quality["average_salary"], 150)
        self.assertEqual(quality["average_rating"], 4)
        self.assertEqual(quality["negative_salary_values"], 1)
        self.assertEqual(quality["zero_salary_values"], 1)
        self.assertEqual(quality["out_of_range_rating_values"], 2)
        self.assertEqual(quality["missing_by_column"]["join_date"], 1)
        self.assertEqual(quality["records_needing_department_standardization"], 3)
        self.assertEqual(quality["department_mapping_review_counts"], {"PM": 1})

    def test_employee_chart_builders_use_paired_and_parseable_records(self):
        dataset = build_employee_dataset(self.employee_result())

        histogram = build_employee_chart(dataset, "histogram")
        correlation = build_employee_chart(dataset, "correlation_heatmap")
        monthly = build_employee_chart(dataset, "monthly_hiring")
        box = build_employee_chart(dataset, "salary_box")

        self.assertEqual(sum(histogram["values"]), 2)
        self.assertEqual(correlation["observations"], 2)
        self.assertEqual(correlation["matrix"][0][1], -1.0)
        self.assertEqual(monthly["type"], "line")
        self.assertEqual(len(monthly["labels"]), 12)
        self.assertEqual(sum(monthly["values"]), 2)
        self.assertTrue(all(group["q1"] <= group["median"] <= group["q3"] for group in box["groups"]))

    def test_employee_analytics_api_returns_tables_and_keeps_answer_on_chart_failure(self):
        with (
            patch.object(assistant_app, "lookup_phone_owner", return_value=None),
            patch.object(assistant_app, "execute_query", return_value=self.employee_result()) as execute_query,
            patch("business_reports.build_employee_chart", side_effect=ValueError("private chart detail")),
        ):
            response = self.client.post("/api/requirement", json={
                "requirement": "Create a histogram of employee salaries.",
            })

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertIn("2 valid observations", payload["explanation"])
        self.assertIsNone(payload["analysis"]["chart"])
        self.assertIn("chart could not be generated", payload["analysis"]["chart_notice"])
        self.assertTrue(payload["report_url"].endswith(".pdf"))
        execute_query.assert_called_once_with(
            "SELECT emp_id, name, department, salary, join_date, city, email, phone, rating, status FROM ncpl_sql.employees_messy",
            select_only=True,
            max_rows=None,
        )

    def test_employee_analysis_survives_pdf_generation_failure(self):
        with (
            patch.object(assistant_app, "lookup_phone_owner", return_value=None),
            patch.object(assistant_app, "execute_query", return_value=self.employee_result()),
            patch.object(assistant_app, "create_business_report_pdf", side_effect=RuntimeError("private PDF detail")),
        ):
            response = self.client.post("/api/requirement", json={
                "requirement": "Assess data quality across employee records.",
            })

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["analysis"]["tables"]), 3)
        self.assertIn("8 rows", payload["explanation"])
        self.assertNotIn("report_url", payload)
        self.assertTrue(any("PDF report could not be prepared" in value for value in payload["analysis"]["limitations"]))

    def test_manual_email_rejects_invalid_recipient(self):
        report_id = assistant_app.store_report({"pdf": b"%PDF-test", "question": "Report"})
        with patch.object(assistant_app, "send_report_email") as send_email:
            response = self.client.post(f"/api/reports/{report_id}/email", json={"email": "not-an-email"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Enter a valid email address for the report.")
        send_email.assert_not_called()

    def test_manual_email_endpoint_sends_existing_report(self):
        report_id = assistant_app.store_report({"pdf": b"%PDF-test", "question": "Report"})
        with patch.object(assistant_app, "send_report_email", return_value="sent") as send_email:
            response = self.client.post(f"/api/reports/{report_id}/email", json={"email": "mary@example.com"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "sent"})
        send_email.assert_called_once_with(b"%PDF-test", "mary@example.com", "Report")

    def test_smtp_sends_pdf_attachment_without_network(self):
        settings = {
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "sender@example.test",
            "SMTP_PASSWORD": "not-a-real-password",
            "SMTP_FROM_EMAIL": "sender@example.test",
            "SMTP_USE_TLS": "true",
            "SMTP_USE_SSL": "false",
        }
        with patch.dict(os.environ, settings), patch("business_reports.smtplib.SMTP") as smtp:
            server = smtp.return_value.__enter__.return_value
            server.send_message.return_value = {}
            result = send_report_email(b"%PDF-test", "recipient@example.test", "Headcount")

        self.assertEqual(result, "sent")
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("sender@example.test", "not-a-real-password")
        message = server.send_message.call_args.args[0]
        attachment = next(message.iter_attachments())
        self.assertEqual(attachment.get_content_type(), "application/pdf")

        with patch.dict(os.environ, settings), patch("business_reports.smtplib.SMTP") as smtp:
            server = smtp.return_value.__enter__.return_value
            server.send_message.return_value = {"recipient@example.test": (550, b"recipient rejected")}
            refused_result = send_report_email(b"%PDF-test", "recipient@example.test", "Headcount")

        self.assertEqual(refused_result, "failed")

    def test_smtp_failure_logs_safe_diagnostic_without_exception_details(self):
        settings = {
            "SMTP_HOST": "smtp.example.test",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "sender@example.test",
            "SMTP_PASSWORD": "not-a-real-password",
            "SMTP_FROM_EMAIL": "sender@example.test",
            "SMTP_USE_TLS": "true",
            "SMTP_USE_SSL": "false",
        }
        with patch.dict(os.environ, settings), patch(
            "business_reports.smtplib.SMTP", side_effect=OSError("private connection detail")
        ):
            with self.assertLogs("business_reports", level="ERROR") as captured:
                result = send_report_email(b"%PDF-test", "recipient@example.test", "Headcount")

        self.assertEqual(result, "failed")
        self.assertIn("OSError", captured.output[0])
        self.assertNotIn("private connection detail", captured.output[0])


if __name__ == "__main__":
    unittest.main()
