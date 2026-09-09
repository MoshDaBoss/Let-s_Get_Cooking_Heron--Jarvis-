const promptForm = document.getElementById('promptForm');
const promptInput = document.getElementById('promptInput');
const nameInput = document.getElementById('nameInput');
const apiKeyInput = document.getElementById('apiKeyInput');
const apiKeyPanel = document.getElementById('apiKeyPanel');
const saveKeyBtn = document.getElementById('saveKeyBtn');
const assistantName = document.getElementById('assistantName');
const brandMark = document.getElementById('brandMark');
const avatarLetter = document.getElementById('avatarLetter');
const chatPanel = document.querySelector('.chat-panel');
const contextPanel = document.getElementById('contextPanel');
const modeButtons = document.querySelectorAll('.mode-btn');
const fileInput = document.getElementById('fileInput');
const linkInput = document.getElementById('linkInput');
const contextList = document.getElementById('contextList');
const addPermanentMemoryBtn = document.getElementById('addPermanentMemoryBtn');
const clearMemoryBtn = document.getElementById('clearMemoryBtn');
const menuButton = document.getElementById('menuButton');
const menuPanel = document.getElementById('menuPanel');
const menuItems = document.querySelectorAll('.menu-item');

const STORAGE_KEY = 'jarvis_persistent_memory';
const contextItems = [];
const persistentContextItems = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');

function mergePersistentContext() {
  persistentContextItems.forEach((item) => {
    if (!contextItems.includes(item)) {
      contextItems.push(item);
    }
  });
}

function updateAssistantName() {
  const name = nameInput.value.trim() || 'JARVIS';
  assistantName.textContent = name;
  nameInput.value = name;
  const initial = name.charAt(0).toUpperCase();
  brandMark.textContent = initial;
  avatarLetter.textContent = initial;
  promptInput.placeholder = `Ask ${name} to help with a task...`;
}

function enableInlineNameEditor() {
  assistantName.addEventListener('dblclick', () => {
    assistantName.classList.add('hidden');
    nameInput.classList.remove('hidden');
    nameInput.focus();
    nameInput.select();
  });

  nameInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      nameInput.blur();
    }
  });

  nameInput.addEventListener('blur', () => {
    const normalized = nameInput.value.trim() || 'JARVIS';
    nameInput.value = normalized;
    updateAssistantName();
    assistantName.classList.remove('hidden');
    nameInput.classList.add('hidden');
  });
}

function loadSavedKey() {
  const saved = localStorage.getItem('gemini_api_key');
  if (saved) {
    apiKeyInput.value = saved;
  }
}

function saveKey() {
  const key = apiKeyInput.value.trim();
  if (key) {
    localStorage.setItem('gemini_api_key', key);
    appendMessage('API key saved locally for this browser. You are welcome, genius.', 'assistant');
  }
}

function persistMemory() {
  const uniqueItems = [...new Set(contextItems)];
  localStorage.setItem(STORAGE_KEY, JSON.stringify(uniqueItems));
}

function removeContextItem(itemToRemove) {
  const index = contextItems.indexOf(itemToRemove);
  if (index >= 0) {
    contextItems.splice(index, 1);
  }
  persistMemory();
  renderContextItems();
}

function renderContextItems() {
  contextList.innerHTML = '';

  if (contextItems.length === 0) {
    contextList.innerHTML = '<p class="empty-state">No context attached yet.</p>';
    return;
  }

  contextItems.forEach((item) => {
    const pill = document.createElement('span');
    pill.className = 'context-pill';

    const label = document.createElement('span');
    label.textContent = item;

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.textContent = '×';
    removeBtn.title = 'Remove from memory';
    removeBtn.addEventListener('click', () => removeContextItem(item));

    pill.appendChild(label);
    pill.appendChild(removeBtn);
    contextList.appendChild(pill);
  });
}

function attachContextFromLinks() {
  const text = linkInput.value.trim();
  if (!text) {
    return;
  }

  const entries = text
    .split(/\n|,/) 
    .map((item) => item.trim())
    .filter(Boolean);

  entries.forEach((entry) => {
    if (!contextItems.includes(entry)) {
      contextItems.push(entry);
    }
  });

  linkInput.value = '';
  persistMemory();
  renderContextItems();
}

async function attachFiles() {
  const files = Array.from(fileInput.files || []);
  if (files.length === 0) {
    return;
  }

  const formData = new FormData();
  files.forEach((file) => formData.append('files', file));

  try {
    const response = await fetch('/api/upload-context', {
      method: 'POST',
      body: formData,
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || 'Unable to parse the uploaded document.');
    }

    (data.documents || []).forEach((document) => {
      const summaryParts = [document.name];
      if (document.summary) {
        summaryParts.push(document.summary);
      }
      if (document.image_summary && document.image_summary.length) {
        summaryParts.push(`Images: ${document.image_summary.join(', ')}`);
      }

      const label = summaryParts.join(' | ');
      if (!contextItems.includes(label)) {
        contextItems.push(label);
      }
    });
  } catch (error) {
    files.forEach((file) => {
      const label = `${file.name} (${file.type || 'file'})`;
      if (!contextItems.includes(label)) {
        contextItems.push(label);
      }
    });
    appendMessage(error.message || 'Document upload could not be parsed fully.', 'assistant');
  }

  fileInput.value = '';
  persistMemory();
  renderContextItems();
}

function appendMessage(text, role = 'assistant') {
  const wrapper = document.createElement('div');
  wrapper.className = `message ${role}`;

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = role === 'assistant' ? assistantName.textContent.charAt(0).toUpperCase() : 'U';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = `<p>${text}</p>`;

  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);
  chatPanel.appendChild(wrapper);
  chatPanel.scrollTop = chatPanel.scrollHeight;
}

function buildPromptWithContext(promptText) {
  if (contextItems.length === 0) {
    return promptText;
  }

  const contextBlock = contextItems.map((item) => `- ${item}`).join('\n');
  return `Task context:\n${contextBlock}\n\nUser request:\n${promptText}`;
}

async function sendPromptToGemini(promptText) {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      prompt: promptText,
      api_key: apiKeyInput.value.trim(),
      context: contextItems,
    }),
  });

  const data = await response.json();

  if (!response.ok) {
    throw new Error(data.error || 'Something went wrong while contacting Gemini.');
  }

  return data.reply;
}

nameInput.addEventListener('input', updateAssistantName);
saveKeyBtn.addEventListener('click', saveKey);

addPermanentMemoryBtn.addEventListener('click', () => {
  persistMemory();
appendMessage('Saved memory entries persist until you remove them. Unlike your excuses, they actually stick around.', 'assistant');
});

clearMemoryBtn.addEventListener('click', () => {
  contextItems.length = 0;
  localStorage.removeItem(STORAGE_KEY);
  renderContextItems();
appendMessage('Saved memory cleared. Fresh start. Try not to make the next one too dramatic.', 'assistant');
});

menuButton.addEventListener('click', () => {
  const expanded = menuButton.getAttribute('aria-expanded') === 'true';
  menuButton.setAttribute('aria-expanded', String(!expanded));
  menuPanel.classList.toggle('hidden', expanded);
});

menuItems.forEach((button) => {
  button.addEventListener('click', () => {
    const target = button.dataset.target;
    menuPanel.classList.add('hidden');
    menuButton.setAttribute('aria-expanded', 'false');

    if (target === 'prompt') {
      promptInput.focus();
      return;
    }

    if (target === 'memories') {
      contextPanel.classList.remove('hidden');
      modeButtons.forEach((btn) => btn.classList.toggle('active', btn.dataset.mode === 'context'));
      return;
    }

    if (target === 'api-key') {
      apiKeyPanel.classList.remove('hidden');
      apiKeyInput.focus();
      apiKeyInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  });
});

modeButtons.forEach((button) => {
  button.addEventListener('click', () => {
    const mode = button.dataset.mode;
    modeButtons.forEach((btn) => btn.classList.toggle('active', btn === button));
    contextPanel.classList.toggle('hidden', mode !== 'context');
  });
});

fileInput.addEventListener('change', attachFiles);
linkInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    attachContextFromLinks();
  }
});

promptForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = promptInput.value.trim();

  if (!text) {
    return;
  }

  appendMessage(text, 'user');
  promptInput.value = '';

  const loading = document.createElement('div');
  loading.className = 'message assistant';
  loading.innerHTML = `
    <div class="avatar">${assistantName.textContent.charAt(0).toUpperCase()}</div>
    <div class="bubble"><p>Thinking...</p></div>
  `;
  chatPanel.appendChild(loading);
  chatPanel.scrollTop = chatPanel.scrollHeight;

  try {
    const enrichedPrompt = buildPromptWithContext(text);
    const reply = await sendPromptToGemini(enrichedPrompt);
    loading.remove();
    appendMessage(reply, 'assistant');
  } catch (error) {
    loading.remove();
    appendMessage(error.message || 'I hit an error while responding.', 'assistant');
  }
});

promptInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    promptForm.requestSubmit();
  }
});

updateAssistantName();
enableInlineNameEditor();
loadSavedKey();
mergePersistentContext();
renderContextItems();
