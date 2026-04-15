const tokenInput = document.getElementById("token");
const saveBtn = document.getElementById("save-btn");
const statusEl = document.getElementById("status");

chrome.storage.sync.get("token", (data) => {
  tokenInput.value = data.token || "";
});

saveBtn.addEventListener("click", () => {
  const token = tokenInput.value.trim();
  chrome.storage.sync.set({ token }, () => {
    statusEl.textContent = "Saved!";
    setTimeout(() => { statusEl.textContent = ""; }, 2000);
  });
});

tokenInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") saveBtn.click();
});
