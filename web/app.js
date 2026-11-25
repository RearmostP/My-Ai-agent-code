const $ = (selector) => document.querySelector(selector);
const chat = $("#chat");
const promptInput = $("#prompt");
const sendButton = $("#send-button");
const pathInput = $("#project-path");
const gitBadge = $("#git-badge");
const settingsDialog = $("#settings-dialog");
const diffDialog = $("#diff-dialog");
let mode = "ai";
let latestDiff = "";
let latestApproval = null;
let busy = false;
let branchNames = { ai: "ai-agent", main: "main" };
let toastTimer;

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove("show"), 2800);
}

function appendMessage(role, text, extraClass = "") {
  const element = document.createElement("article");
  element.className = `message ${role} ${extraClass}`.trim();
  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = role === "user" ? "אתם" : "סוכן הקוד";
  const body = document.createElement("div");
  body.textContent = text;
  element.append(label, body);
  chat.append(element);
  chat.scrollTop = chat.scrollHeight;
  return { element, body };
}

function setBusy(value) {
  busy = value;
  sendButton.disabled = value;
  sendButton.textContent = value ? "…" : "↑";
  promptInput.disabled = value;
}

function updateBranchLabels() {
  const aiButton = document.querySelector('.mode-option[data-mode="ai"]');
  const mainButton = document.querySelector('.mode-option[data-mode="main"]');
  const aiCode = document.createElement("code");
  aiCode.textContent = `(${branchNames.ai})`;
  aiButton.replaceChildren(document.createTextNode("ענף AI ייעודי "), aiCode);
  const mainCode = document.createElement("code");
  mainCode.textContent = `(${branchNames.main})`;
  mainButton.replaceChildren(document.createTextNode("ענף ראשי "), mainCode);
  $("#approve-button").textContent = `אשר ודחוף ל-${branchNames.main}`;
}

async function updateGitStatus() {
  const path = pathInput.value.trim();
  if (!path) {
    gitBadge.textContent = "בחרו פרויקט";
    gitBadge.className = "git-badge";
    return;
  }
  try {
    const response = await fetch(`/api/git-status?path=${encodeURIComponent(path)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "לא ניתן לקרוא מצב Git.");
    gitBadge.className = `git-badge ${data.is_git ? "is-git" : "no-git"}`;
    gitBadge.textContent = data.is_git ? `Git · ${data.branch}` : "פרויקט ללא Git";
  } catch (error) {
    gitBadge.textContent = "נתיב לא זמין";
    gitBadge.className = "git-badge";
  }
}

function showDiff(diff) {
  latestDiff = diff || "";
  const container = $("#diff-content");
  container.replaceChildren();
  if (!latestDiff) {
    const empty = document.createElement("p");
    empty.className = "empty-diff";
    empty.textContent = "עדיין אין שינויים להצגה.";
    container.append(empty);
    $("#diff-open").disabled = true;
    return;
  }
  $("#diff-open").disabled = false;
  latestDiff.split("\n").forEach((line) => {
    const row = document.createElement("div");
    row.className = "diff-line";
    row.textContent = line || " ";
    if (line.startsWith("+") && !line.startsWith("+++")) row.classList.add("add");
    else if (line.startsWith("-") && !line.startsWith("---")) row.classList.add("remove");
    else if (line.startsWith("@@") || line.startsWith("---") || line.startsWith("+++")) row.classList.add("header");
    container.append(row);
  });
}

function connectReasoning(message, value) {
  let details = message.querySelector("details.reasoning");
  if (!details) {
    details = document.createElement("details");
    details.className = "reasoning";
    const summary = document.createElement("summary");
    summary.textContent = "מחשבת המודל (Reasoning)";
    const body = document.createElement("div");
    body.className = "reasoning-body";
    details.append(summary, body);
    message.append(details);
  }
  details.querySelector(".reasoning-body").textContent += value;
}

async function submitPrompt() {
  const path = pathInput.value.trim();
  const prompt = promptInput.value.trim();
  if (busy) return;
  if (!path) return toast("יש לבחור נתיב לפרויקט.");
  if (!prompt) return;
  const welcome = chat.querySelector(".welcome");
  if (welcome) welcome.remove();
  appendMessage("user", prompt);
  const response = appendMessage("assistant", "בודק את הפרויקט ומכין שינויים…", "waiting");
  promptInput.value = "";
  latestApproval = null;
  $("#approval-panel").hidden = true;
  setBusy(true);
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, prompt, mode }),
    });
    if (!res.ok) {
      const error = await res.json();
      throw new Error(error.detail || "הבקשה נכשלה.");
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let completed = false;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let split;
      while ((split = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        const eventName = block.match(/^event: (.+)$/m)?.[1];
        const dataLine = block.match(/^data: (.+)$/m)?.[1];
        if (!eventName || !dataLine) continue;
        const data = JSON.parse(dataLine);
        if (eventName === "reasoning") connectReasoning(response.element, data.text);
        if (eventName === "approval") {
          latestApproval = data.approval_id;
          $("#approval-message").textContent = data.message;
          $("#approval-panel").hidden = false;
        }
        if (eventName === "complete") {
          completed = true;
          response.body.textContent = data.files.length
            ? `עודכנו ${data.files.length} קבצים: ${data.files.map((file) => `\`${file}\``).join(", ")}\n\n${data.summary}`
            : data.summary;
          response.element.classList.remove("waiting");
          if (data.reasoning) connectReasoning(response.element, data.reasoning);
          showDiff(data.diff);
          updateGitStatus();
        }
        if (eventName === "error") throw new Error(data.message);
      }
    }
    if (!completed) throw new Error("החיבור הסתיים לפני שהתקבלה תשובה מלאה.");
  } catch (error) {
    response.body.textContent = error.message;
    response.element.classList.remove("waiting");
    response.element.classList.add("error");
  } finally {
    setBusy(false);
    promptInput.focus();
  }
}

$("#prompt-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitPrompt();
});
promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    submitPrompt();
  }
});
document.querySelectorAll(".mode-option").forEach((button) => {
  button.addEventListener("click", () => {
    mode = button.dataset.mode;
    document.querySelectorAll(".mode-option").forEach((option) => option.classList.toggle("active", option === button));
  });
});
pathInput.addEventListener("change", updateGitStatus);
$("#refresh-status").addEventListener("click", updateGitStatus);
$("#settings-open").addEventListener("click", async () => {
  try {
    const response = await fetch("/api/settings");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "לא ניתן לטעון הגדרות.");
    const rules = data.rules;
    branchNames = { ai: rules.ai_branch_name, main: rules.main_branch_name };
    updateBranchLabels();
    $("#system-rules").value = Array.isArray(rules.system_rules) ? rules.system_rules.join("\n") : rules.system_rules;
    $("#external-rules").value = rules.external_rules_file_path || "";
    $("#ai-branch").value = rules.ai_branch_name;
    $("#main-branch").value = rules.main_branch_name;
    $("#ignored-paths").value = rules.ignored_paths.join("\n");
    $("#model-label").textContent = `${data.model_name}${data.api_key_configured ? "" : " · חסר API key"}`;
    settingsDialog.showModal();
  } catch (error) { toast(error.message); }
});
$("#settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    ai_branch_name: $("#ai-branch").value.trim(),
    main_branch_name: $("#main-branch").value.trim(),
    system_rules: $("#system-rules").value.split("\n").map((line) => line.trim()).filter(Boolean),
    external_rules_file_path: $("#external-rules").value.trim(),
    ignored_paths: $("#ignored-paths").value.split("\n").map((line) => line.trim()).filter(Boolean),
  };
  try {
    const response = await fetch("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "שמירת ההגדרות נכשלה.");
    branchNames = { ai: payload.ai_branch_name, main: payload.main_branch_name };
    updateBranchLabels();
    $("#settings-message").textContent = data.message;
    setTimeout(() => { if (settingsDialog.open) settingsDialog.close(); }, 650);
  } catch (error) { $("#settings-message").textContent = error.message; }
});
document.querySelectorAll(".close-dialog").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
$("#diff-open").addEventListener("click", () => diffDialog.showModal());
$("#approve-button").addEventListener("click", async () => {
  if (!latestApproval) return;
  try {
    const response = await fetch("/api/approve", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approval_id: latestApproval }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "האישור נכשל.");
    $("#approval-panel").hidden = true;
    latestApproval = null;
    toast(data.message);
    updateGitStatus();
  } catch (error) { toast(error.message); }
});
$("#cancel-approval").addEventListener("click", async () => {
  if (!latestApproval) return;
  try {
    const response = await fetch("/api/cancel-approval", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approval_id: latestApproval }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "לא ניתן לבטל את האישור.");
    $("#approval-panel").hidden = true;
    latestApproval = null;
    toast(data.message);
  } catch (error) { toast(error.message); }
});
