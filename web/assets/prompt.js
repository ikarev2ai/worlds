(() => {
  "use strict";

  const button = document.getElementById("copy-prompt");
  const prompt = document.getElementById("agent-prompt-text");
  const status = document.getElementById("copy-status");
  if (!button || !prompt || !status) return;

  function fallbackCopy(text) {
    const field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.className = "clipboard-field";
    document.body.append(field);
    field.select();
    const copied = document.execCommand("copy");
    field.remove();
    if (!copied) throw new Error("Copy command was rejected");
  }

  async function copyPrompt() {
    try {
      const text = prompt.textContent;
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        fallbackCopy(text);
      }
      status.textContent = "Промпт скопирован.";
    } catch (error) {
      status.textContent = "Не удалось скопировать автоматически. Выделите текст промпта вручную.";
    }
  }

  button.hidden = false;
  button.addEventListener("click", copyPrompt);
})();
