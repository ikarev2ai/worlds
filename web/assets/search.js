(() => {
  "use strict";

  const form = document.getElementById("search-form");
  const input = document.getElementById("search-input");
  const results = document.getElementById("search-results");
  const rootMeta = document.querySelector('meta[name="site-root"]');
  if (!form || !input || !results || !rootMeta) return;

  const siteRoot = rootMeta.content;
  const kindLabels = {
    home: "UWI",
    ontology: "Онтология",
    questions: "Вопросы",
    worlds_index: "Каталог",
    world: "Мир",
    work: "Произведение",
    prompts_index: "Промпты",
    prompt: "Промпт",
  };
  let documentsPromise;

  const normalize = (value) => value.toLocaleLowerCase("ru-RU").replaceAll("ё", "е");

  function loadDocuments() {
    if (!documentsPromise) {
      documentsPromise = fetch(`${siteRoot}search-index.json`, { credentials: "same-origin" })
        .then((response) => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          return response.json();
        })
        .then((items) => items.map((item) => ({
          ...item,
          normalizedTitle: normalize(item.title),
          normalizedText: normalize(item.text),
        })));
    }
    return documentsPromise;
  }

  function snippet(item, terms) {
    const text = item.text.replace(/\s+/g, " ").trim();
    const normalizedText = normalize(text);
    const positions = terms.map((term) => normalizedText.indexOf(term)).filter((position) => position >= 0);
    const center = positions.length ? Math.min(...positions) : 0;
    const start = Math.max(0, center - 70);
    const end = Math.min(text.length, start + 190);
    return `${start > 0 ? "…" : ""}${text.slice(start, end).trim()}${end < text.length ? "…" : ""}`;
  }

  function render(items, terms) {
    results.replaceChildren();
    const summary = document.createElement("p");
    summary.textContent = items.length
      ? `Найдено: ${items.length}${items.length === 20 ? " (показаны первые 20)" : ""}`
      : "Ничего не найдено. Попробуйте изменить запрос.";
    results.append(summary);

    items.slice(0, 20).forEach((item) => {
      const link = document.createElement("a");
      link.className = "search-result";
      link.href = siteRoot + item.url;

      const title = document.createElement("strong");
      title.textContent = item.title;
      const meta = document.createElement("span");
      meta.className = "result-meta";
      meta.textContent = item.parent ? `${kindLabels[item.kind] || item.kind} · ${item.parent}` : (kindLabels[item.kind] || item.kind);
      const text = document.createElement("span");
      text.className = "result-snippet";
      text.textContent = snippet(item, terms);

      link.append(title, meta, text);
      results.append(link);
    });
    results.hidden = false;
  }

  async function search() {
    const query = normalize(input.value.trim());
    if (query.length < 2) {
      results.hidden = true;
      results.replaceChildren();
      return;
    }
    const terms = query.split(/\s+/).filter(Boolean);
    try {
      const documents = await loadDocuments();
      const matches = documents.filter((item) => terms.every((term) =>
        item.normalizedTitle.includes(term) || item.normalizedText.includes(term)
      ));
      matches.sort((left, right) => {
        const score = (item) => {
          let value = 0;
          if (item.normalizedTitle === query) value += 200;
          terms.forEach((term) => {
            if (item.normalizedTitle.includes(term)) value += 50;
            if (item.normalizedText.includes(term)) value += 10;
          });
          return value;
        };
        return score(right) - score(left) || left.title.localeCompare(right.title, "ru");
      });
      if (input.value.trim() && normalize(input.value.trim()) === query) render(matches, terms);
    } catch (error) {
      results.replaceChildren();
      const message = document.createElement("p");
      message.textContent = "Не удалось загрузить поисковый индекс. Обновите страницу и попробуйте снова.";
      results.append(message);
      results.hidden = false;
    }
  }

  form.hidden = false;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    search();
  });
  input.addEventListener("input", search);
})();
