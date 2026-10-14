const form = document.querySelector('#query-form');
const input = document.querySelector('#query-input');
const messages = document.querySelector('#messages');
const emptyState = document.querySelector('#empty-state');
const sendButton = document.querySelector('#send-button');
const micButton = document.querySelector('#mic-button');
const voiceStatus = document.querySelector('#voice-status');
const newChatButton = document.querySelector('#new-chat');
const historyList = document.querySelector('#conversation-history');
const shell = document.querySelector('#assistant-shell');
const sidebarToggle = document.querySelector('#sidebar-toggle');
const sidebarCollapse = document.querySelector('#sidebar-collapse');
const sidebarBackdrop = document.querySelector('#sidebar-backdrop');
const storageKey = 'employeeInsights.conversations.v1';
const maxHistoryMessages = 8;
const maxConversations = 30;
const mobileSidebar = window.matchMedia('(max-width: 760px)');
let conversations = loadConversations();
let currentConversationId = null;
let activeRequest = null;
let recognition = null;
let recording = false;
let nextQuestionIsVoice = false;

function loadConversations() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || '[]');
    if (!Array.isArray(saved)) return [];
    return saved.filter((conversation) =>
      conversation && typeof conversation.id === 'string'
      && typeof conversation.title === 'string' && Array.isArray(conversation.messages)
    ).map((conversation) => ({
      id: conversation.id,
      title: conversation.title,
      updatedAt: Number(conversation.updatedAt) || Date.now(),
      messages: conversation.messages.filter((message) =>
        message && ['user', 'assistant'].includes(message.role) && typeof message.content === 'string'
      ),
    })).slice(0, maxConversations);
  } catch {
    return [];
  }
}

function saveConversations() {
  try {
    localStorage.setItem(storageKey, JSON.stringify(conversations.slice(0, maxConversations)));
  } catch {
    setVoiceStatus('This conversation could not be saved on this device.');
  }
}

function currentConversation() {
  return conversations.find((conversation) => conversation.id === currentConversationId);
}

function renderHistory() {
  historyList.replaceChildren();
  [...conversations].sort((left, right) => right.updatedAt - left.updatedAt).forEach((conversation) => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'history-item';
    item.classList.toggle('is-active', conversation.id === currentConversationId);
    item.textContent = conversation.title;
    item.title = conversation.title;
    item.setAttribute('aria-current', conversation.id === currentConversationId ? 'page' : 'false');
    item.addEventListener('click', () => openConversation(conversation.id));
    historyList.append(item);
  });
}

function makeConversation(requirement) {
  const id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const title = requirement.replace(/\s+/g, ' ').trim();
  const conversation = {
    id,
    title: title.length > 48 ? `${title.slice(0, 47)}…` : title,
    updatedAt: Date.now(),
    messages: [],
  };
  conversations.unshift(conversation);
  conversations = conversations.slice(0, maxConversations);
  currentConversationId = id;
  return conversation;
}

function createUserMessage(content) {
  const article = document.createElement('article');
  article.className = 'chat-message chat-user';
  const label = document.createElement('div');
  label.className = 'chat-message-label';
  label.textContent = 'You';
  const text = document.createElement('p');
  text.className = 'chat-user-text';
  text.textContent = content;
  article.append(label, text);
  return article;
}

function createAssistantMessage(content, options = {}) {
  const article = document.createElement('article');
  article.className = 'chat-message chat-assistant';
  const label = document.createElement('div');
  label.className = 'chat-message-label assistant-label';
  const mark = document.createElement('span');
  mark.className = 'assistant-mark';
  mark.setAttribute('aria-hidden', 'true');
  mark.textContent = 'E';
  const name = document.createElement('span');
  name.textContent = 'Employee Insights Assistant';
  label.append(mark, name);

  const answer = document.createElement('div');
  answer.className = options.error ? 'chat-answer chat-error' : 'chat-answer';
  const text = document.createElement('p');
  text.className = 'chat-answer-text';
  text.textContent = content;
  answer.append(text);
  article.append(label, answer);

  if (options.speak) addStopAudioButton(article, text.textContent);
  return article;
}

function addStopAudioButton(parent, content) {
  stopAudio();
  if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
    setVoiceStatus('Audio playback isn’t available in this browser.');
    return;
  }
  const actions = document.createElement('div');
  actions.className = 'answer-actions';
  const stopButton = document.createElement('button');
  stopButton.type = 'button';
  stopButton.className = 'stop-audio-button';
  stopButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2"></rect></svg><span>Stop audio</span>';
  stopButton.addEventListener('click', stopAudio);
  actions.append(stopButton);
  parent.append(actions);

  try {
    const utterance = new SpeechSynthesisUtterance(content);
    utterance.lang = navigator.language || 'en-US';
    utterance.onend = () => { stopButton.hidden = true; };
    utterance.onerror = () => { stopButton.hidden = true; };
    window.speechSynthesis.speak(utterance);
  } catch {
    stopButton.hidden = true;
    setVoiceStatus('Audio playback isn’t available right now.');
  }
}

function stopAudio() {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  document.querySelectorAll('.stop-audio-button').forEach((button) => { button.hidden = true; });
}

function appendConversationMessage(conversation, role, content) {
  conversation.messages.push({ role, content });
  conversation.updatedAt = Date.now();
  saveConversations();
  renderHistory();
}

function renderConversation(conversation) {
  messages.replaceChildren();
  emptyState.hidden = conversation.messages.length > 0;
  conversation.messages.forEach((message) => {
    messages.append(message.role === 'user'
      ? createUserMessage(message.content)
      : createAssistantMessage(message.content));
  });
  messages.scrollTop = messages.scrollHeight;
}

function cancelActiveRequest() {
  activeRequest?.abort();
  activeRequest = null;
  sendButton.disabled = false;
}

function openConversation(id) {
  cancelActiveRequest();
  stopAudio();
  const conversation = conversations.find((item) => item.id === id);
  if (!conversation) return;
  currentConversationId = id;
  renderConversation(conversation);
  renderHistory();
  closeMobileSidebar();
  input.focus();
}

function startNewChat() {
  cancelActiveRequest();
  stopAudio();
  currentConversationId = null;
  nextQuestionIsVoice = false;
  messages.replaceChildren();
  emptyState.hidden = false;
  input.value = '';
  resizeInput();
  setVoiceStatus('');
  renderHistory();
  closeMobileSidebar();
  input.focus();
}

function setVoiceStatus(message) {
  voiceStatus.textContent = message;
}

function resizeInput() {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}

function scrollToLatest() {
  messages.scrollTop = messages.scrollHeight;
}

async function runRequirement(requirement, voiceOrigin) {
  let conversation = currentConversation();
  if (!conversation) conversation = makeConversation(requirement);
  const history = conversation.messages.slice(-maxHistoryMessages);
  currentConversationId = conversation.id;
  emptyState.hidden = true;
  messages.append(createUserMessage(requirement));
  appendConversationMessage(conversation, 'user', requirement);
  input.value = '';
  resizeInput();
  setVoiceStatus('');

  const assistant = document.createElement('article');
  assistant.className = 'chat-message chat-assistant';
  const label = document.createElement('div');
  label.className = 'chat-message-label assistant-label';
  const mark = document.createElement('span');
  mark.className = 'assistant-mark';
  mark.setAttribute('aria-hidden', 'true');
  mark.textContent = 'E';
  const name = document.createElement('span');
  name.textContent = 'Employee Insights Assistant';
  label.append(mark, name);
  const loading = document.createElement('p');
  loading.className = 'chat-thinking';
  loading.textContent = 'Thinking…';
  assistant.append(label, loading);
  messages.append(assistant);
  scrollToLatest();

  const controller = new AbortController();
  activeRequest = controller;
  sendButton.disabled = true;
  try {
    const response = await fetch('/api/requirement', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ requirement, history }),
      signal: controller.signal,
    });
    if (activeRequest !== controller || currentConversationId !== conversation.id) return;
    const payload = await response.json();
    if (activeRequest !== controller || currentConversationId !== conversation.id) return;
    loading.remove();

    let reply;
    let error = false;
    if (!response.ok) {
      reply = 'I couldn’t complete that just now. Please try again.';
      error = true;
    } else if (payload.clarification) {
      reply = payload.clarification;
    } else if (payload.explanation) {
      reply = payload.explanation;
    } else {
      reply = 'I found the information, but couldn’t provide a clear answer just now. Please try again.';
      error = true;
    }

    const answer = createAssistantMessage(reply, { error });
    assistant.replaceWith(answer);
    appendConversationMessage(conversation, 'assistant', reply);
    if (voiceOrigin && !error) addStopAudioButton(answer, reply);
    scrollToLatest();
  } catch (error) {
    if (error.name === 'AbortError' || activeRequest !== controller) return;
    loading.remove();
    const reply = 'I couldn’t complete that just now. Please try again.';
    const answer = createAssistantMessage(reply, { error: true });
    assistant.replaceWith(answer);
    appendConversationMessage(conversation, 'assistant', reply);
    scrollToLatest();
  } finally {
    if (activeRequest === controller) {
      activeRequest = null;
      sendButton.disabled = false;
    }
  }
}

function closeMobileSidebar() {
  if (mobileSidebar.matches) {
    shell.classList.remove('mobile-sidebar-open');
    updateSidebarButton();
  }
}

function toggleSidebar() {
  if (mobileSidebar.matches) {
    shell.classList.toggle('mobile-sidebar-open');
  } else {
    shell.classList.toggle('sidebar-collapsed');
  }
  updateSidebarButton();
}

function updateSidebarButton() {
  if (mobileSidebar.matches) {
    shell.classList.remove('sidebar-collapsed');
    const isOpen = shell.classList.contains('mobile-sidebar-open');
    sidebarToggle.setAttribute('aria-label', isOpen ? 'Close sidebar' : 'Open sidebar');
    sidebarToggle.setAttribute('aria-expanded', String(isOpen));
    sidebarCollapse.setAttribute('aria-label', isOpen ? 'Close sidebar' : 'Open sidebar');
    sidebarCollapse.title = isOpen ? 'Close sidebar' : 'Open sidebar';
  } else {
    shell.classList.remove('mobile-sidebar-open');
    const isExpanded = !shell.classList.contains('sidebar-collapsed');
    sidebarToggle.setAttribute('aria-label', isExpanded ? 'Collapse sidebar' : 'Expand sidebar');
    sidebarToggle.setAttribute('aria-expanded', String(isExpanded));
    sidebarCollapse.setAttribute('aria-label', isExpanded ? 'Collapse sidebar' : 'Expand sidebar');
    sidebarCollapse.title = isExpanded ? 'Collapse sidebar' : 'Expand sidebar';
  }
}

function toggleRecording() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    setVoiceStatus('Voice input isn’t available in this browser. You can type your question instead.');
    return;
  }
  if (recording && recognition) {
    recognition.stop();
    return;
  }

  recognition = new Recognition();
  recognition.lang = navigator.language || 'en-US';
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  recognition.onstart = () => {
    recording = true;
    micButton.classList.add('is-recording');
    micButton.setAttribute('aria-pressed', 'true');
    micButton.setAttribute('aria-label', 'Stop voice input');
    micButton.title = 'Stop voice input';
    setVoiceStatus('Listening…');
  };
  recognition.onresult = (event) => {
    const transcript = event.results?.[0]?.[0]?.transcript?.trim();
    if (!transcript) {
      setVoiceStatus('I didn’t catch that. Please try again.');
      return;
    }
    input.value = transcript;
    resizeInput();
    nextQuestionIsVoice = true;
    setVoiceStatus('Sending your question…');
    form.requestSubmit();
  };
  recognition.onerror = () => {
    setVoiceStatus('Voice input couldn’t start. You can type your question instead.');
  };
  recognition.onend = () => {
    recording = false;
    micButton.classList.remove('is-recording');
    micButton.setAttribute('aria-pressed', 'false');
    micButton.setAttribute('aria-label', 'Start voice input');
    micButton.title = 'Start voice input';
    if (voiceStatus.textContent === 'Listening…') setVoiceStatus('');
  };
  try {
    recognition.start();
  } catch {
    recording = false;
    setVoiceStatus('Voice input couldn’t start. You can type your question instead.');
  }
}

form.addEventListener('submit', (event) => {
  event.preventDefault();
  const requirement = input.value.trim();
  const voiceOrigin = nextQuestionIsVoice;
  nextQuestionIsVoice = false;
  if (!requirement || activeRequest) return;
  stopAudio();
  runRequirement(requirement, voiceOrigin);
});

input.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});
input.addEventListener('input', resizeInput);
micButton.addEventListener('click', toggleRecording);
newChatButton.addEventListener('click', startNewChat);
sidebarToggle.addEventListener('click', toggleSidebar);
sidebarCollapse.addEventListener('click', toggleSidebar);
sidebarBackdrop.addEventListener('click', closeMobileSidebar);
mobileSidebar.addEventListener('change', updateSidebarButton);

renderHistory();
updateSidebarButton();
resizeInput();
