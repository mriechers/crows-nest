const API_URL = "https://share.bymarkriechers.com/api/ingest";

const pageUrlEl = document.getElementById("page-url");
const contextInput = document.getElementById("context");
const saveBtn = document.getElementById("save-btn");
const statusEl = document.getElementById("status");
const mainEl = document.getElementById("main");
const noTokenEl = document.getElementById("no-token");
const openOptionsEl = document.getElementById("open-options");

let currentUrl = "";
let token = "";

openOptionsEl.addEventListener("click", () => {
  chrome.runtime.openOptionsPage();
});

// Submit on Enter in the context field
contextInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !saveBtn.disabled) {
    saveBtn.click();
  }
});

chrome.storage.sync.get("token", (data) => {
  token = (data.token || "").trim();
  if (!token) {
    mainEl.style.display = "none";
    noTokenEl.style.display = "block";
    return;
  }

  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    currentUrl = tabs[0]?.url || "";
    pageUrlEl.textContent = currentUrl;
  });
});

saveBtn.addEventListener("click", async () => {
  if (!currentUrl || !token) return;

  saveBtn.disabled = true;
  saveBtn.textContent = "Saving...";
  statusEl.textContent = "";
  statusEl.className = "status";

  const context = contextInput.value.trim() || undefined;

  try {
    const res = await fetch(API_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ url: currentUrl, source: "extension", context }),
    });

    const data = await res.json();

    if (data.error) {
      statusEl.textContent = `Error: ${data.error}`;
      statusEl.className = "status error";
      saveBtn.disabled = false;
      saveBtn.textContent = "Save to Pipeline";
    } else {
      statusEl.textContent = `Queued (#${data.id})`;
      statusEl.className = "status success";
      saveBtn.textContent = "Saved!";
      setTimeout(() => window.close(), 1200);
    }
  } catch (err) {
    statusEl.textContent = `Failed: ${err.message}`;
    statusEl.className = "status error";
    saveBtn.disabled = false;
    saveBtn.textContent = "Save to Pipeline";
  }
});
