/* Колода внутри приложения.

   Раньше колода была отдельной страницей, и это ломалось именно в Telegram:
   подпись приходит в адресе при открытии, а уход на другой документ её
   затирает — кнопки есть, за ними пусто. Поэтому колода стала таким же
   экраном, как остальные, а 78 карт грузятся при первом открытии вкладки,
   чтобы не висеть грузом на запуске.

   Модуль ничего не знает о вкладках: ему дают узел, он рисует в него. */

(() => {
  "use strict";

  const GOLD = "var(--deck-gold)";
  const DARK = "#4A2E18";

  // Telegram кэширует мини-приложение надолго: метка версии из адреса
  // приписывается к картинкам, и перерисованная карта доезжает.
  const STAMP = new URLSearchParams(location.search).get("v");
  const V = STAMP ? `?v=${encodeURIComponent(STAMP)}` : "";

  const TONES = {
    fool: { bg: "#b98d5b", ink: DARK, back: "backs/back-fool.jpg" },
    major1: { bg: "#8f0303", ink: GOLD, back: "backs/back-major1.jpg" },
    major2: { bg: "#101a33", ink: GOLD, back: "backs/back-major2.jpg" },
    major3: { bg: "#15120f", ink: GOLD, back: "backs/back-major3.jpg" },
    wands: { bg: "#6d1a01", ink: GOLD, back: "backs/back-wands.jpg" },
    cups: { bg: "#123a4a", ink: GOLD, back: "backs/back-cups.jpg" },
    swords: { bg: "#29232b", ink: GOLD, back: "backs/back-swords.jpg" },
    pentacles: { bg: "#262813", ink: GOLD, back: "backs/back-pentacles.jpg" },
  };

  const SUIT_TITLES = {
    major: "Старшие арканы",
    wands: "Жезлы",
    cups: "Кубки",
    swords: "Мечи",
    pentacles: "Пентакли",
  };

  // Нарисованные карты: лицо берётся из cards/, у остальных поле пустое
  const PAINTED = new Set(
    []
      .concat(Array.from({ length: 22 }, (_, i) => `major_${String(i).padStart(2, "0")}`))
      .concat(
        ["wands", "cups", "swords", "pentacles"].flatMap((suit) =>
          Array.from({ length: 14 }, (_, i) => `${suit}_${String(i + 1).padStart(2, "0")}`)
        )
      )
  );

  function toneFor(card) {
    if (card.group !== "major") return TONES[card.group];
    if (card.order === 0) return TONES.fool;
    if (card.order <= 7) return TONES.major1;
    if (card.order <= 14) return TONES.major2;
    return TONES.major3;
  }

  // Имя занимает максимум две строки: одно слово — цифра на вторую строку,
  // два слова — вторым словом, цифра сразу за ним.
  function nameLines(card, withNumeral) {
    const words = card.name.toUpperCase().split(/\s+/);
    const numeral =
      withNumeral && card.numeral ? `<span class="numeral">(${card.numeral})</span>` : "";
    if (words.length === 1) return [words[0], numeral];
    const [first, ...rest] = words;
    return [first, [rest.join(" "), numeral].filter(Boolean).join(" ")];
  }

  function nameHtml(card, withNumeral) {
    const [one, two] = nameLines(card, withNumeral);
    return (
      `<div class="name">${one}</div>` + (two ? `<div class="name second">${two}</div>` : "")
    );
  }

  const motto = (card) =>
    card.mini.split(",")[0].trim().split(/\s+/).slice(0, 2).join(" ");

  function cardHtml(card) {
    const tone = toneFor(card);
    const painted = PAINTED.has(card.id) ? `cards/${card.id}.jpg${V}` : null;
    return `
      <button class="dcard" aria-label="${card.name}" aria-pressed="false">
        <div class="dside face${painted ? "" : " pending"}" style="--tone:${tone.bg};--card-ink:${tone.ink}">
          ${painted ? `<img src="${painted}" alt="${card.name}" loading="lazy">` : ""}
          <div class="dtitle">${nameHtml(card, false)}</div>
          ${painted ? "" : '<div class="waiting-mark">не нарисована</div>'}
        </div>
        <div class="dside dback" style="--card-ink:${tone.ink}">
          <img src="${tone.back}${V}" alt="Рубашка карты" loading="lazy">
          <div class="copy">
            <div>${nameHtml(card, true)}<div class="motto">${motto(card)}</div></div>
            <div class="card-text">${card.text}</div>
          </div>
        </div>
      </button>`;
  }

  let cards = [];
  let filter = "all";
  let viewer = null;

  function haptic(style) {
    const tg = window.Telegram && window.Telegram.WebApp;
    if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred(style);
  }

  /* --- разворот карты ------------------------------------------------------
     Живёт поверх всего приложения и закрывается системной кнопкой «назад»:
     внутри Telegram это первое, что нажимают. */

  function openCard(card) {
    closeCard();
    viewer = document.createElement("div");
    viewer.className = "dviewer";
    viewer.innerHTML = `
      <button class="dclose" aria-label="Закрыть">✕</button>
      <div class="dslot">${cardHtml(card)}</div>
      <div class="dhint">Нажмите на карту, чтобы перевернуть</div>`;
    document.body.append(viewer);

    const big = viewer.querySelector(".dcard");
    const hint = viewer.querySelector(".dhint");
    big.addEventListener("click", () => {
      haptic("light");
      const flipped = big.classList.toggle("flipped");
      big.setAttribute("aria-pressed", String(flipped));
      hint.textContent = flipped ? "Обратная сторона" : "Нажмите на карту, чтобы перевернуть";
    });

    viewer.querySelector(".dclose").addEventListener("click", closeCard);
    viewer.addEventListener("click", (event) => {
      if (event.target === viewer) closeCard();
    });
    document.addEventListener("keydown", onEscape);
    window.DeckScreen.onViewerOpen();
  }

  function closeCard() {
    if (!viewer) return;
    viewer.remove();
    viewer = null;
    document.removeEventListener("keydown", onEscape);
    window.DeckScreen.onViewerClose();
  }

  function onEscape(event) {
    if (event.key === "Escape") closeCard();
  }

  /* --- доска --------------------------------------------------------------- */

  function draw(root) {
    const grid = root.querySelector("#deck-grid");
    const count = root.querySelector("#deck-count");
    grid.textContent = "";

    const shown = cards.filter(
      (c) => filter === "all" || (filter === "major" ? c.group === "major" : c.group !== "major")
    );

    let group = null;
    for (const card of shown) {
      if (card.group !== group) {
        group = card.group;
        const heading = document.createElement("div");
        heading.className = "group-title";
        heading.textContent = SUIT_TITLES[group] || group;
        grid.append(heading);
      }
      const slot = document.createElement("div");
      slot.className = "dslot";
      slot.innerHTML = cardHtml(card);
      slot.querySelector(".dcard").addEventListener("click", () => {
        haptic("light");
        openCard(card);
      });
      grid.append(slot);
    }

    count.textContent = `${shown.length} карт · нарисовано ${
      cards.filter((c) => PAINTED.has(c.id)).length
    }`;
  }

  async function render(root) {
    root.innerHTML = `
      <h2>Колода на столе</h2>
      <p class="lead">Все 78 карт в порядке колоды. Нажмите на карту — она откроется
        крупно, и там её можно перевернуть: на рубашке имя, цифра аркана, краткое
        и подробное значение.</p>
      <div class="deck-tools">
        <button class="chip on" data-filter="all">Все 78</button>
        <button class="chip" data-filter="major">Старшие арканы</button>
        <button class="chip" data-filter="minor">Младшие</button>
        <span id="deck-count" class="note"></span>
      </div>
      <div class="deck-sizes">
        <button class="chip" data-size="128">Мелко</button>
        <button class="chip on" data-size="180">Средне</button>
        <button class="chip" data-size="240">Крупно</button>
      </div>
      <div class="deck-grid" id="deck-grid"><div class="skeleton"></div></div>`;

    root.querySelectorAll("[data-filter]").forEach((chip) =>
      chip.addEventListener("click", () => {
        haptic("light");
        filter = chip.dataset.filter;
        root.querySelectorAll("[data-filter]").forEach((c) => c.classList.toggle("on", c === chip));
        draw(root);
      })
    );

    root.querySelectorAll("[data-size]").forEach((chip) =>
      chip.addEventListener("click", () => {
        haptic("light");
        document.documentElement.style.setProperty("--card-min", `${chip.dataset.size}px`);
        root.querySelectorAll("[data-size]").forEach((c) => c.classList.toggle("on", c === chip));
      })
    );

    if (!cards.length) {
      try {
        cards = await (await fetch(`cards.json${V}`)).json();
      } catch (err) {
        root.querySelector("#deck-grid").innerHTML =
          '<p class="note">Не удалось загрузить колоду.</p>';
        return;
      }
    }
    draw(root);
  }

  // Приложение зовёт render и спрашивает, открыт ли разворот: системная
  // кнопка «назад» должна сперва закрыть карту, а не уводить с экрана.
  window.DeckScreen = {
    render,
    close: closeCard,
    isOpen: () => viewer !== null,
    onViewerOpen: () => {},
    onViewerClose: () => {},
  };
})();
