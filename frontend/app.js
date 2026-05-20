const state = {
  settings: null,
  models: [],
  chats: [],
  activeChat: null,
  engine: null,
  context: null,
  webEnabled: false,
  streamingId: null,
  pendingChunks: new Map(),
  streamText: new Map(),
  streamPollers: new Map(),
  streamNotices: new Map(),
};

const $ = (id) => document.getElementById(id);
let mockApi = null;

if (new URLSearchParams(location.search).get("mock") === "1") {
  mockApi = buildMockBridge();
  setTimeout(() => window.dispatchEvent(new Event("pywebviewready")), 0);
}

function api() {
  if (mockApi) return mockApi;
  if (!window.pywebview || !window.pywebview.api) {
    throw new Error("VLite must be run through the desktop app.");
  }
  return window.pywebview.api;
}

function buildMockBridge() {
  const sampleChat = {
    id: "chat1",
    title: "Neural interface draft",
    uploads: [{ name: "notes.pdf", kind: "document" }],
    messages: [
      { role: "user", content: "Can you summarize this local document?" },
      { role: "assistant", content: "Yes. The document context is attached locally and the response will stay inside this machine unless web tools are enabled." },
    ],
  };
  return {
      bootstrap: async () => ({
        settings: {
          server_path: "",
          thinking_enabled: false,
          show_thinking: false,
          model_folders: ["C:/Users/mat76/Downloads"],
          load: {
            ctx_size: 8192,
            gpu_layers: -1,
            batch: 1024,
            ubatch: 512,
            threads: 0,
            split_mode: "layer",
            type_k: "",
            type_v: "",
            flash_attn: true,
            mmap: true,
            mlock: false,
            cache_prompt: true,
            no_warmup: false,
            mmproj_offload: true,
            chat_template: "",
            chat_template_kwargs: "",
            reasoning: "auto",
            reasoning_budget: -1,
            reasoning_format: "auto",
            extra_flags: "",
          },
          generation: {
            max_tokens: 1600,
            temperature: 0.7,
            top_p: 0.95,
            top_k: 40,
            min_p: 0.05,
            typical_p: 1,
            repeat_penalty: 1.1,
            presence_penalty: 0,
            frequency_penalty: 0,
            mirostat: 0,
            stop: "",
          },
        },
        models: [{ id: "m1", name: "gemma3-local-q4", vision: true, projectors: [{ path: "mmproj.gguf" }] }],
        folders: ["C:/Users/mat76/Downloads"],
        chats: [sampleChat],
        active_chat: sampleChat,
        engine: { loaded: true, model: { name: "gemma3-local-q4", vision_loaded: false }, last_error: "" },
        context: { label: "842 / 8,192", percent: 10.2, exact: false },
      }),
      create_chat: async () => ({ chat: sampleChat, chats: [sampleChat], context: { label: "0 / 8,192", percent: 0, exact: false } }),
      select_chat: async () => ({ chat: sampleChat, chats: [sampleChat], context: { label: "842 / 8,192", percent: 10.2, exact: false } }),
      delete_chat: async () => ({ success: true, chat: sampleChat, chats: [sampleChat], context: { label: "842 / 8,192", percent: 10.2, exact: false } }),
      export_chat: async () => ({ success: true, path: "mock-chat.txt" }),
      toggle_fullscreen: async () => ({ success: true }),
      scan_models: async () => ({ models: [{ id: "m1", name: "gemma3-local-q4", vision: true, projectors: [{ path: "mmproj.gguf" }] }], folders: ["C:/Users/mat76/Downloads"] }),
      add_model_folder: async () => ({ cancelled: true }),
      save_settings: async (payload) => ({ settings: payload, engine: { loaded: true, model: { name: "gemma3-local-q4" } } }),
      load_model: async () => ({ success: true, status: { loaded: true, model: { name: "gemma3-local-q4" } } }),
      unload_model: async () => ({ success: true, status: { loaded: false } }),
      upload_files: async () => ({ cancelled: true }),
      poll_stream: async (messageId, cursor = 0) => {
        const chunks = ["This ", "is ", "streaming."];
        const elapsed = Date.now() - Number(messageId.split("_")[1] || Date.now());
        const available = elapsed > 220 ? 3 : elapsed > 140 ? 2 : elapsed > 70 ? 1 : 0;
        const done = elapsed > 300;
        return {
          found: true,
          chunks: chunks.slice(cursor, available),
          cursor: available,
          done,
          chat: done ? { ...sampleChat, messages: [...sampleChat.messages, { role: "user", content: "stream test" }, { role: "assistant", content: "This is streaming." }] } : null,
          chats: done ? [{ ...sampleChat, messages: [...sampleChat.messages, { role: "user", content: "stream test" }, { role: "assistant", content: "This is streaming." }] }] : null,
          context: done ? { label: "900 / 8,192", percent: 11, exact: false } : null,
        };
      },
      send_message: async (text) => {
        const messageId = `mock_${Date.now()}`;
        const chat = {
          ...sampleChat,
          messages: [...sampleChat.messages, { role: "user", content: text }],
        };
        return { queued: true, chat, chats: [chat], context: { label: "880 / 8,192", percent: 10.7, exact: false }, message_id: messageId };
      },
  };
}

window.addEventListener("pywebviewready", async () => {
  bindEvents();
  try {
    const data = await api().bootstrap();
    applyBootstrap(data);
  } catch (error) {
    showNotice(error.message || String(error));
  }
});

window.VLiteEvents = {
  handle(payload) {
    if (payload.event === "engine") {
      state.engine = payload.engine;
      renderEngine();
    }
    if (payload.event === "chat") {
      state.activeChat = payload.chat;
      state.chats = payload.chats;
      renderChats();
      renderMessages();
    }
    if (payload.event === "context") {
      state.context = payload.context;
      renderContext();
    }
    if (payload.event === "stream_start") {
      state.streamingId = payload.message_id;
      appendStreamingMessage(payload.message_id);
      startPollingStream(payload.message_id);
    }
    if (payload.event === "stream_chunk") {
      addStreamChunk(payload.message_id, payload.chunk);
    }
    if (payload.event === "stream_complete") {
      const completedId = payload.message_id || state.streamingId;
      if (completedId) {
        state.pendingChunks.delete(completedId);
        state.streamText.delete(completedId);
      }
      state.streamingId = null;
      state.activeChat = payload.chat;
      state.chats = payload.chats;
      state.context = payload.context;
      renderChats();
      renderMessages();
      renderContext();
    }
  }
};

function bindEvents() {
  $("toggleSidebarBtn").onclick = () => $("app").classList.toggle("sidebar-closed");
  $("settingsBtn").onclick = () => document.body.classList.add("settings-open");
  $("closeSettingsBtn").onclick = closeSettings;
  $("drawerBackdrop").onclick = closeSettings;
  $("newChatBtn").onclick = async () => applyChatResult(await api().create_chat());
  $("rescanBtn").onclick = async () => {
    showNotice("Scanning model folders...");
    const result = await api().scan_models();
    state.models = result.models;
    renderModels();
    renderFolders(result.folders);
    showNotice(`Found ${state.models.length} models.`);
  };
  $("addFolderBtn").onclick = async () => {
    const result = await api().add_model_folder();
    if (!result.cancelled) {
      state.settings = result.settings;
      state.models = result.models;
      renderSettings();
      renderModels();
      renderFolders(result.folders);
    }
  };
  $("saveSettingsBtn").onclick = saveSettings;
  $("loadTextBtn").onclick = () => loadSelected("text");
  $("loadVisionBtn").onclick = () => loadSelected("vision");
  $("unloadBtn").onclick = async () => {
    const result = await api().unload_model();
    state.engine = result.status;
    renderEngine();
  };
  $("uploadBtn").onclick = async () => {
    const result = await api().upload_files();
    if (!result.cancelled) applyChatResult(result);
    if (result.errors && result.errors.length) showNotice(result.errors.join(" | "));
  };
  $("webBtn").onclick = () => {
    state.webEnabled = !state.webEnabled;
    $("webBtn").classList.toggle("active", state.webEnabled);
  };
  $("sendBtn").onclick = sendMessage;
  $("promptInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
  $("promptInput").addEventListener("input", autoGrow);
  window.addEventListener("keydown", async (event) => {
    if (event.key === "F11") {
      event.preventDefault();
      try {
        await api().toggle_fullscreen();
      } catch (error) {
        showNotice(error.message || String(error));
      }
    }
  });
}

function closeSettings() {
  document.body.classList.remove("settings-open");
}

function applyBootstrap(data) {
  state.settings = data.settings;
  state.models = data.models;
  state.chats = data.chats;
  state.activeChat = data.active_chat;
  state.engine = data.engine;
  state.context = data.context;
  renderSettings();
  renderFolders(data.folders);
  renderModels();
  renderChats();
  renderMessages();
  renderEngine();
  renderContext();
  const params = new URLSearchParams(location.search);
  if (mockApi && params.get("autostream") === "1") {
    setTimeout(() => {
      $("promptInput").value = "stream test";
      sendMessage();
    }, 50);
  }
}

function applyChatResult(result) {
  state.activeChat = result.chat;
  state.chats = result.chats;
  state.context = result.context || state.context;
  renderChats();
  renderMessages();
  renderContext();
}

function renderModels() {
  const select = $("modelSelect");
  select.innerHTML = "";
  if (!state.models.length) {
    select.append(new Option("No GGUF models found", ""));
    return;
  }
  state.models.forEach((model) => {
    const badge = model.vision ? "  [vision]" : "";
    select.append(new Option(`${model.name}${badge}`, model.id));
  });
}

function selectedModel() {
  return state.models.find((model) => model.id === $("modelSelect").value);
}

async function loadSelected(mode) {
  const model = selectedModel();
  if (!model) return showNotice("Select a model first.");
  if (mode === "vision" && !model.vision) return showNotice("No compatible mmproj file was found for this model.");
  showNotice(mode === "vision" ? "Loading vision model..." : "Loading text model...");
  const projector = mode === "vision" ? (model.projectors[0] && model.projectors[0].path) : "";
  const result = await api().load_model(model.id, mode, projector);
  state.engine = result.status;
  renderEngine();
  showNotice(result.success ? "Model loaded." : result.error);
}

function renderEngine() {
  const status = $("engineStatus");
  const model = state.engine && state.engine.model;
  if (state.engine && state.engine.loaded && model) {
    status.textContent = `${model.name}${model.vision_loaded ? " + vision" : ""}`;
    status.style.color = "var(--ok)";
  } else {
    status.textContent = state.engine && state.engine.last_error ? "Runtime issue" : "No model loaded";
    status.style.color = "var(--muted)";
  }
}

function renderChats() {
  const list = $("chatList");
  list.innerHTML = "";
  state.chats.forEach((chat) => {
    const row = document.createElement("div");
    row.className = `chat-row ${state.activeChat && chat.id === state.activeChat.id ? "active" : ""}`;
    const button = document.createElement("button");
    button.className = "chat-item";
    button.textContent = chat.title || "New Chat";
    button.title = button.textContent;
    button.onclick = async () => applyChatResult(await api().select_chat(chat.id));
    const deleteButton = document.createElement("button");
    deleteButton.className = "chat-delete";
    deleteButton.title = "Delete chat";
    deleteButton.innerHTML = '<svg viewBox="0 0 24 24"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v5"/><path d="M14 11v5"/></svg>';
    deleteButton.onclick = async (event) => {
      event.stopPropagation();
      applyChatResult(await api().delete_chat(chat.id));
    };
    const exportButton = document.createElement("button");
    exportButton.className = "chat-export";
    exportButton.title = "Export chat";
    exportButton.innerHTML = '<svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>';
    exportButton.onclick = async (event) => {
      event.stopPropagation();
      const result = await api().export_chat(chat.id);
      if (result.success) {
        showNotice("Chat exported.");
      } else if (!result.cancelled) {
        showNotice(result.error || "Chat export failed.");
      }
    };
    row.append(button, exportButton, deleteButton);
    list.append(row);
  });
}

function renderMessages() {
  const messages = $("messages");
  messages.innerHTML = "";
  const items = state.activeChat ? state.activeChat.messages || [] : [];
  $("emptyState").style.display = items.length ? "none" : "grid";
  items.forEach((message) => {
    messages.append(messageNode(message.role, message.content));
  });
  renderUploads();
  messages.scrollTop = messages.scrollHeight;
}

function messageNode(role, text, id) {
  const node = document.createElement("div");
  node.className = `message ${role}`;
  if (id) node.dataset.streamId = id;
  node.textContent = text || "";
  return node;
}

function appendStreamingMessage(id) {
  if (!id || document.querySelector(`[data-stream-id="${id}"]`)) return;
  $("emptyState").style.display = "none";
  $("messages").append(messageNode("assistant", "", id));
  const pending = state.pendingChunks.get(id);
  if (pending) {
    pending.forEach((chunk) => addStreamChunk(id, chunk));
    state.pendingChunks.delete(id);
  }
}

function addStreamChunk(id, chunk) {
  const node = document.querySelector(`[data-stream-id="${id}"]`);
  if (!node) {
    const pending = state.pendingChunks.get(id) || [];
    pending.push(chunk);
    state.pendingChunks.set(id, pending);
    return;
  }
  const next = (state.streamText.get(id) || node.textContent || "") + chunk;
  state.streamText.set(id, next);
  node.textContent = next;
  $("messages").scrollTop = $("messages").scrollHeight;
}

function renderUploads() {
  const box = $("uploads");
  box.innerHTML = "";
  const uploads = state.activeChat ? state.activeChat.uploads || [] : [];
  uploads.forEach((upload) => {
    const chip = document.createElement("span");
    chip.className = "upload-chip";
    chip.textContent = upload.kind === "image" ? `Image: ${upload.name}` : `File: ${upload.name}`;
    box.append(chip);
  });
}

function renderContext() {
  const context = state.context || { label: "0", percent: 0, exact: false };
  $("contextLabel").textContent = `${context.label} tokens${context.exact ? "" : " estimated"}`;
  $("contextFill").style.width = `${Math.min(context.percent || 0, 100)}%`;
}

async function sendMessage() {
  const input = $("promptInput");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  autoGrow();
  const result = await api().send_message(text, state.webEnabled);
  if (!result.queued) {
    showNotice(result.error || "Message was not queued.");
    return;
  }
  applyChatResult(result);
  if (result.message_id) {
    state.streamingId = result.message_id;
    appendStreamingMessage(result.message_id);
    startPollingStream(result.message_id);
  }
  if (result.web_status) {
    showNotice(result.web_status);
  }
}

function startPollingStream(messageId) {
  if (!messageId || state.streamPollers.has(messageId)) return;
  let cursor = 0;
  const poll = async () => {
    try {
      const result = await api().poll_stream(messageId, cursor);
      if (result.notice && state.streamNotices.get(messageId) !== result.notice) {
        state.streamNotices.set(messageId, result.notice);
        showNotice(result.notice);
      }
      if (result.found) {
        (result.chunks || []).forEach((chunk) => addStreamChunk(messageId, chunk));
        cursor = Number(result.cursor || cursor);
      }
      if (result.done) {
        stopPollingStream(messageId);
        if (result.chat && result.chats) {
          state.activeChat = result.chat;
          state.chats = result.chats;
          state.context = result.context || state.context;
          renderChats();
          renderMessages();
          renderContext();
        }
      }
    } catch (error) {
      stopPollingStream(messageId);
      showNotice(error.message || String(error));
    }
  };
  state.streamPollers.set(messageId, window.setInterval(poll, 50));
  poll();
}

function stopPollingStream(messageId) {
  const timer = state.streamPollers.get(messageId);
  if (timer) window.clearInterval(timer);
  state.streamPollers.delete(messageId);
  state.pendingChunks.delete(messageId);
  state.streamText.delete(messageId);
  state.streamNotices.delete(messageId);
}

function renderSettings() {
  const s = state.settings || {};
  const load = s.load || {};
  const gen = s.generation || {};
  $("serverPathInput").value = s.server_path || "";
  setValue("ctxSizeInput", load.ctx_size);
  setValue("gpuLayersInput", load.gpu_layers);
  setValue("batchInput", load.batch);
  setValue("ubatchInput", load.ubatch);
  setValue("threadsInput", load.threads);
  setValue("splitModeInput", load.split_mode);
  setValue("typeKInput", load.type_k);
  setValue("typeVInput", load.type_v);
  setChecked("flashInput", load.flash_attn);
  setChecked("mmapInput", load.mmap);
  setChecked("mlockInput", load.mlock);
  setChecked("cachePromptInput", load.cache_prompt);
  setChecked("noWarmupInput", load.no_warmup);
  setChecked("mmprojOffloadInput", load.mmproj_offload);
  setValue("chatTemplateInput", load.chat_template);
  setValue("templateKwargsInput", load.chat_template_kwargs);
  setValue("reasoningInput", load.reasoning);
  setValue("reasoningBudgetInput", load.reasoning_budget);
  setValue("reasoningFormatInput", load.reasoning_format);
  setValue("extraFlagsInput", load.extra_flags);
  setValue("maxTokensInput", gen.max_tokens);
  setValue("temperatureInput", gen.temperature);
  setValue("topPInput", gen.top_p);
  setValue("topKInput", gen.top_k);
  setValue("minPInput", gen.min_p);
  setValue("typicalPInput", gen.typical_p);
  setValue("repeatPenaltyInput", gen.repeat_penalty);
  setValue("presencePenaltyInput", gen.presence_penalty);
  setValue("frequencyPenaltyInput", gen.frequency_penalty);
  setValue("mirostatInput", gen.mirostat);
  setValue("stopInput", gen.stop);
  setChecked("thinkingInput", s.thinking_enabled);
  setChecked("showThinkingInput", s.show_thinking);
}

function renderFolders(folders) {
  const box = $("folderList");
  box.innerHTML = "";
  (folders || []).forEach((folder) => {
    const item = document.createElement("div");
    item.textContent = folder;
    box.append(item);
  });
}

async function saveSettings() {
  const payload = {
    server_path: $("serverPathInput").value.trim(),
    thinking_enabled: $("thinkingInput").checked,
    show_thinking: $("showThinkingInput").checked,
    load: {
      ctx_size: numberValue("ctxSizeInput"),
      gpu_layers: numberValue("gpuLayersInput"),
      batch: numberValue("batchInput"),
      ubatch: numberValue("ubatchInput"),
      threads: numberValue("threadsInput"),
      split_mode: $("splitModeInput").value,
      type_k: $("typeKInput").value.trim(),
      type_v: $("typeVInput").value.trim(),
      flash_attn: $("flashInput").checked,
      mmap: $("mmapInput").checked,
      mlock: $("mlockInput").checked,
      cache_prompt: $("cachePromptInput").checked,
      no_warmup: $("noWarmupInput").checked,
      mmproj_offload: $("mmprojOffloadInput").checked,
      chat_template: $("chatTemplateInput").value.trim(),
      chat_template_kwargs: $("templateKwargsInput").value.trim(),
      reasoning: $("reasoningInput").value,
      reasoning_budget: numberValue("reasoningBudgetInput"),
      reasoning_format: $("reasoningFormatInput").value.trim() || "auto",
      extra_flags: $("extraFlagsInput").value.trim(),
    },
    generation: {
      max_tokens: numberValue("maxTokensInput"),
      temperature: numberValue("temperatureInput"),
      top_p: numberValue("topPInput"),
      top_k: numberValue("topKInput"),
      min_p: numberValue("minPInput"),
      typical_p: numberValue("typicalPInput"),
      repeat_penalty: numberValue("repeatPenaltyInput"),
      presence_penalty: numberValue("presencePenaltyInput"),
      frequency_penalty: numberValue("frequencyPenaltyInput"),
      mirostat: numberValue("mirostatInput"),
      stop: $("stopInput").value,
    },
  };
  const result = await api().save_settings(payload);
  state.settings = result.settings;
  state.engine = result.engine;
  renderEngine();
  showNotice("Settings saved.");
}

function setValue(id, value) { $(id).value = value ?? ""; }
function setChecked(id, value) { $(id).checked = Boolean(value); }
function numberValue(id) {
  const value = $(id).value;
  return value === "" ? 0 : Number(value);
}

function autoGrow() {
  const input = $("promptInput");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

function showNotice(text) {
  $("notice").textContent = text || "";
  if (text) setTimeout(() => {
    if ($("notice").textContent === text) $("notice").textContent = "";
  }, 6000);
}
