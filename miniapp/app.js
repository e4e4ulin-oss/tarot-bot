/* Оболочка мини-приложения: вкладки, запросы к API, отрисовка экранов.

   Приложение заменяет бота, поэтому любое действие здесь — это тот же вызов,
   который раньше делала кнопка в переписке. Данные приходят с нашего же
   сервера: статику и /api отдаёт один домен, значит ни CORS, ни токенов в
   коде не нужно — подпись Telegram уезжает в заголовке. */

(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const INIT = (tg && tg.initData) || "";

  // --- подготовка окна Telegram ---------------------------------------------

  if (tg) {
    tg.ready();
    tg.expand();
    // Иначе свайп вниз по экрану закрывает приложение прямо посреди расклада
    if (tg.disableVerticalSwipes) tg.disableVerticalSwipes();
    if (tg.setHeaderColor) tg.setHeaderColor("#080d1f");
    if (tg.setBackgroundColor) tg.setBackgroundColor("#080d1f");
  }

  const tap = (style = "light") => {
    if (tg && tg.HapticFeedback) tg.HapticFeedback.impactOccurred(style);
  };

  // --- запросы --------------------------------------------------------------

  async function api(path, options = {}) {
    const res = await fetch(`/api/${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        // Без подписи сервер не ответит ничем, кроме health и config
        Authorization: `tma ${INIT}`,
        ...(options.headers || {}),
      },
    });
    let data = null;
    try {
      data = await res.json();
    } catch (err) {
      data = null;
    }
    if (!res.ok) {
      const message = (data && data.message) || `Ошибка ${res.status}`;
      const error = new Error(message);
      error.status = res.status;
      throw error;
    }
    return data;
  }

  // --- мелкие помощники -----------------------------------------------------

  const $ = (sel) => document.querySelector(sel);
  const esc = (value) =>
    String(value == null ? "" : value).replace(
      /[&<>"']/g,
      (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]
    );

  const plural = (n, one, few, many) => {
    const mod10 = n % 10;
    const mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
    return many;
  };

  const dateLabel = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
  };

  function fail(node, error) {
    const retry = error && error.status === 401;
    node.innerHTML = `
      <div class="card">
        <h3>Не получилось загрузить</h3>
        <p>${esc(error ? error.message : "Неизвестная ошибка")}</p>
        ${retry ? '<p class="note">Закройте и откройте приложение заново.</p>' : ""}
      </div>`;
  }

  // --- вкладки --------------------------------------------------------------

  const screens = Array.from(document.querySelectorAll(".screen"));
  const tabs = Array.from(document.querySelectorAll(".tab"));
  const loaded = new Set();

  function show(name) {
    if (name === "deck") {
      // Колода — отдельная страница: она самодостаточна и уже умеет всё,
      // что нужно, а держать её внутри оболочки значит грузить 78 картинок
      // вместе с каждым запуском приложения
      location.href = "deck.html";
      return;
    }
    screens.forEach((s) => s.classList.toggle("on", s.dataset.tab === name));
    tabs.forEach((t) => t.classList.toggle("on", t.dataset.go === name));
    window.scrollTo({ top: 0 });
    if (!loaded.has(name)) {
      loaded.add(name);
      render[name]();
    }
  }

  tabs.forEach((btn) =>
    btn.addEventListener("click", () => {
      tap();
      show(btn.dataset.go);
    })
  );

  // --- экраны ---------------------------------------------------------------

  let config = null;

  const render = {
    async daily() {
      const body = $("#daily-body");
      try {
        const data = await api("daily");
        if (!data.card) {
          body.innerHTML = `
            <div class="empty">
              <img src="assets/empty-card.webp" alt="">
              <p>Карта на сегодня ещё не вытянута.</p>
            </div>
            <button class="btn" id="daily-draw">Вытянуть карту</button>`;
          $("#daily-draw").addEventListener("click", async (event) => {
            event.target.disabled = true;
            tap("medium");
            try {
              const drawn = await api("daily", { method: "POST" });
              body.innerHTML = dailyCard(drawn.card, drawn.subscribed);
              bindSubscribe();
            } catch (err) {
              fail(body, err);
            }
          });
          return;
        }
        body.innerHTML = dailyCard(data.card, data.subscribed);
        bindSubscribe();
      } catch (err) {
        if (err.status === 404) {
          body.innerHTML = `<div class="card"><h3>Скоро</h3><p>Карта дня переезжает сюда из бота.</p></div>`;
          return;
        }
        fail(body, err);
      }
    },

    async spread() {
      const body = $("#spread-body");
      try {
        const data = config || (await loadConfig());
        body.innerHTML = data.spreads
          .map(
            (s) => `
            <div class="card tap" data-spread="${esc(s.key)}">
              <h3>${esc(s.title)}</h3>
              <p>${esc(s.subtitle)}</p>
              <p class="note">${s.size} ${plural(s.size, "карта", "карты", "карт")}</p>
            </div>`
          )
          .join("");
        body.querySelectorAll("[data-spread]").forEach((node) =>
          node.addEventListener("click", () => {
            tap();
            // Экран вопроса и сама тасовка — следующий шаг переезда
            if (tg && tg.showPopup) {
              tg.showPopup({
                title: "Скоро",
                message: "Расклад переезжает из бота следующим шагом.",
                buttons: [{ type: "ok" }],
              });
            }
          })
        );
      } catch (err) {
        fail(body, err);
      }
    },

    async author() {
      const body = $("#author-body");
      try {
        const data = config || (await loadConfig());
        $("#author-lead").textContent = `Разбор делает ${data.author} — живой человек, а не модель.`;
        if (!data.tiers.length) {
          body.innerHTML = `<div class="card"><h3>Пока закрыто</h3><p>Автор не принимает заявки.</p></div>`;
          return;
        }
        body.innerHTML = data.tiers
          .map(
            (tier) => `
            <div class="card">
              <h3>${esc(tier.title)} · ${tier.price} ${esc(data.currency)}</h3>
              <p>${esc(tier.summary)}</p>
              <ul class="note" style="margin:10px 0 0;padding-left:18px">
                ${tier.includes.map((line) => `<li>${esc(line)}</li>`).join("")}
              </ul>
              <p class="note">ответ ${esc(tier.hours)} ч · уточнений: ${tier.followups}</p>
            </div>`
          )
          .join("");
      } catch (err) {
        fail(body, err);
      }
    },

    async profile() {
      const body = $("#profile-body");
      try {
        const me = await api("me");
        const r = me.readings;
        const name = me.user.first_name || "Гость";
        const birth = me.birth.date
          ? `${dateLabel(me.birth.date)}${me.birth.time ? ", " + esc(me.birth.time) : ""}`
          : "не заполнено";

        const history = me.history.length
          ? me.history
              .map(
                (item) => `
              <div class="row">
                <span>${esc(item.spread || item.mode)}</span>
                <b>${esc(dateLabel(item.created_at))}</b>
              </div>`
              )
              .join("")
          : `<div class="row"><span>Раскладов пока нет</span><b>—</b></div>`;

        body.innerHTML = `
          <div class="card">
            <h3>${esc(name)}</h3>
            <p>${me.user.username ? "@" + esc(me.user.username) : "без имени пользователя"}</p>
          </div>

          <div class="rows" style="margin-bottom:12px">
            <div class="row"><span>Бесплатных раскладов</span><b>${r.free_left} из ${r.free_limit}</b></div>
            <div class="row"><span>Всего раскладов</span><b>${r.used}</b></div>
            <div class="row"><span>Карта дня по утрам</span><b>${me.daily_subscribed ? "включена" : "выключена"}</b></div>
            <div class="row"><span>Дата рождения</span><b>${birth}</b></div>
            <div class="row"><span>Город</span><b>${esc(me.city || me.birth.city || "не указан")}</b></div>
          </div>

          <h3 style="font-family:var(--serif);font-weight:500;margin:18px 2px 10px">История</h3>
          <div class="rows">${history}</div>
          ${
            me.open_order
              ? `<p class="note">Заявка №${me.open_order.id} в работе с ${esc(dateLabel(me.open_order.created_at))}.</p>`
              : ""
          }`;
      } catch (err) {
        fail(body, err);
      }
    },
  };

  function dailyCard(card, subscribed) {
    return `
      <figure class="shot${card.reversed ? " upside" : ""}">
        <img src="${esc(card.image)}" alt="${esc(card.name)}" loading="lazy">
      </figure>
      <div class="card">
        <h3>${esc(card.name)}${card.reversed ? " · перевёрнутая" : ""}</h3>
        <p>${esc(card.text || card.mini || "")}</p>
        ${card.keywords && card.keywords.length ? `<p class="note">${esc(card.keywords.join(" · "))}</p>` : ""}
      </div>
      ${card.advice ? `<div class="card"><h3>Совет</h3><p>${esc(card.advice)}</p></div>` : ""}
      <button class="btn ghost" id="daily-sub">
        ${subscribed ? "Отключить утреннюю карту" : "Присылать карту по утрам"}
      </button>`;
  }

  function bindSubscribe() {
    const btn = $("#daily-sub");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      tap();
      try {
        const res = await api("daily/subscription", { method: "POST" });
        btn.textContent = res.subscribed
          ? "Отключить утреннюю карту"
          : "Присылать карту по утрам";
      } catch (err) {
        btn.textContent = "Не получилось, попробуйте позже";
      } finally {
        btn.disabled = false;
      }
    });
  }

  async function loadConfig() {
    config = await api("config");
    if (config.author) {
      $("#brand").textContent = config.author;
    }
    return config;
  }

  // --- старт ----------------------------------------------------------------

  loadConfig().catch(() => {});
  show("daily");
})();
