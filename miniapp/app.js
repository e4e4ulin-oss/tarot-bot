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

    // Во весь экран: карты и разбор занимают всю высоту, а не две трети.
    // Доступно с Bot API 8.0 и только на телефонах — на компьютере окно
    // приложения и так своё, и разворачивать там нечего.
    const fullscreenReady =
      typeof tg.requestFullscreen === "function" &&
      typeof tg.isVersionAtLeast === "function" &&
      tg.isVersionAtLeast("8.0");
    const phone = ["android", "ios"].includes(String(tg.platform || "").toLowerCase());

    if (fullscreenReady && phone && !tg.isFullscreen) {
      try {
        tg.requestFullscreen();
      } catch (err) {
        // Отказали — приложение просто останется развёрнутым, как было
      }
    }

    // В полноэкранном режиме кнопки Telegram висят поверх страницы: отступы
    // приходят отдельными переменными, и без них шапка уезжает под них.
    const syncInsets = () => {
      document.documentElement.classList.toggle("fullscreen", Boolean(tg.isFullscreen));
    };
    syncInsets();
    if (tg.onEvent) {
      tg.onEvent("fullscreenChanged", syncInsets);
      tg.onEvent("fullscreenFailed", syncInsets);
      tg.onEvent("safeAreaChanged", syncInsets);
      tg.onEvent("contentSafeAreaChanged", syncInsets);
    }
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
    // Разворот карты закрываем до перехода: иначе он останется поверх
    // нового экрана и перехватит первое же нажатие
    if (window.DeckScreen && window.DeckScreen.isOpen()) window.DeckScreen.close();
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
    async deck() {
      // 78 карт грузятся при первом открытии вкладки, а не на старте.
      // Модуля может не оказаться, если Telegram отдал страницу из кэша
      // вперемешку со старыми файлами — тогда честно скажем об этом.
      if (!window.DeckScreen) {
        $("#s-deck").innerHTML =
          '<div class="card"><h3>Колода не загрузилась</h3>' +
          "<p>Закройте и откройте приложение заново.</p></div>";
        return;
      }
      await window.DeckScreen.render($("#s-deck"));
    },

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
      await spreadList();
    },

    async author() {
      await authorView();
    },

    async profile() {
      await profileView();
    },
  };

  /* --- кабинет: данные рождения, натальная карта, прогноз ------------------
     Небо считается по дате, времени и городу: без них карта не строится,
     поэтому кабинет и начинается с них. */

  const profileRender = {
    async main() {
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
              <button class="row tap" data-reading="${item.id}">
                <span>${esc(item.title || item.spread || item.mode)}</span>
                <b>${esc(dateLabel(item.created_at))} ›</b>
              </button>`
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

          <div class="card tap" data-go="edit">
            <h3>Данные рождения</h3>
            <p>${me.birth.date ? "Изменить дату, время и город" : "Заполните — и небо посчитается"}</p>
          </div>
          <div class="card tap" data-go="chart">
            <h3>Натальная карта</h3>
            <p>Солнце, Луна, асцендент и аспекты</p>
          </div>
          <div class="card tap" data-go="forecast">
            <h3>Прогноз</h3>
            <p>Что происходит с вашей картой сейчас</p>
          </div>
          <div class="card tap" data-go="natal">
            <h3>Подробный разбор карты</h3>
            <p>Пишется один раз и надолго</p>
          </div>
          <div class="card tap" data-go="invite">
            <h3>Пригласить друга</h3>
            <p>Бесплатные расклады вам и ему${r.bonus ? ` · сейчас ${r.bonus} в запасе` : ""}</p>
          </div>
          ${
            me.admin
              ? `<div class="card tap" data-go="promos">
                   <h3>Промокоды</h3>
                   <p>Завести код и посмотреть, сколько раз его взяли</p>
                 </div>`
              : ""
          }

          <h3 style="font-family:var(--serif);font-weight:500;margin:18px 2px 10px">История</h3>
          <div class="rows">${history}</div>
          ${
            me.open_order
              ? `<p class="note">Заявка №${me.open_order.id} в работе с ${esc(dateLabel(me.open_order.created_at))}.</p>`
              : ""
          }`;

        body.querySelectorAll("[data-go]").forEach((node) =>
          node.addEventListener("click", () => {
            tap();
            profileView(node.dataset.go, me);
          })
        );

        body.querySelectorAll("[data-reading]").forEach((node) =>
          node.addEventListener("click", () => {
            tap();
            profileRender.reading(node.dataset.reading);
          })
        );
      } catch (err) {
        fail(body, err);
      }
    },

    async edit(me) {
      const body = $("#profile-body");
      const birth = (me && me.birth) || {};
      body.innerHTML = `
        ${backLink("В кабинет", "p-back")}
        <h3 style="font-family:var(--serif);font-weight:500;font-size:22px;margin:0 0 4px">Данные рождения</h3>
        <p class="lead">Всё необязательно. Чем больше известно, тем точнее карта: без времени не будет домов, без города — асцендента.</p>
        <label class="note">Дата рождения</label>
        <input class="field one-line plain" id="p-date"
               placeholder="25.07.1992" value="${esc(birth.date ? isoToRu(birth.date) : "")}">
        <label class="note">Время рождения</label>
        <input class="field one-line plain" id="p-time"
               placeholder="14:30" value="${esc(birth.time || "")}">
        <label class="note">Город рождения</label>
        <input class="field one-line plain" id="p-city"
               placeholder="Саратов" value="${esc(birth.city || "")}">
        <div id="p-cities"></div>
        <button class="btn" id="p-save" style="margin-top:6px">Сохранить</button>
        <p class="note" id="p-msg"></p>`;

      $("#p-back").addEventListener("click", () => {
        tap();
        profileView("main");
      });

      $("#p-save").addEventListener("click", async () => {
        const button = $("#p-save");
        button.disabled = true;
        tap();
        try {
          const saved = await api("profile", {
            method: "PUT",
            body: JSON.stringify({
              birth_date: $("#p-date").value,
              birth_time: $("#p-time").value,
              birth_city: $("#p-city").value,
              place_id: chosenPlace,
            }),
          });
          if (saved.ambiguous) {
            await showNamesakes($("#p-city").value);
            $("#p-msg").textContent = "Городов с таким названием несколько — выберите свой.";
          } else {
            $("#p-msg").textContent = "Сохранено.";
            loaded.delete("profile");
            setTimeout(() => profileView("main"), 700);
          }
        } catch (err) {
          $("#p-msg").textContent = err.message;
        } finally {
          button.disabled = false;
        }
      });
    },

    async chart() {
      const body = $("#profile-body");
      body.innerHTML = `${backLink("В кабинет", "p-back")}<p class="waiting">Считаю карту…</p>`;
      $("#p-back").addEventListener("click", () => {
        tap();
        profileView("main");
      });
      try {
        const data = await api("chart");
        const tail = !data.ready
          ? `<div class="card"><h3>Нужна дата рождения</h3><p>Заполните её в кабинете — и карта посчитается.</p></div>`
          : `<div class="card"><div class="reading">${data.lines.map(esc).join("\n")}</div>
               ${data.has_houses ? "" : '<p class="note">Без точного времени и города дома не считаются — показаны только планеты и аспекты.</p>'}
             </div>`;
        body.innerHTML = `${backLink("В кабинет", "p-back")}
          <h3 style="font-family:var(--serif);font-weight:500;font-size:22px;margin:0 0 10px">Натальная карта</h3>
          ${tail}`;
        $("#p-back").addEventListener("click", () => {
          tap();
          profileView("main");
        });
      } catch (err) {
        fail(body, err);
      }
    },

    async forecast() {
      const body = $("#profile-body");
      const head = `${backLink("В кабинет", "p-back")}
        <h3 style="font-family:var(--serif);font-weight:500;font-size:22px;margin:0 0 10px">Прогноз</h3>`;
      body.innerHTML = `${head}<p class="waiting">Смотрю транзиты…</p>`;
      $("#p-back").addEventListener("click", () => {
        tap();
        profileView("main");
      });

      try {
        const data = await api("forecast");
        if (!data.ready) {
          body.innerHTML = `${head}<div class="card"><h3>Нужна дата рождения</h3><p>Без неё смотреть не на что.</p></div>`;
          bindProfileBack();
          return;
        }
        if (data.quiet) {
          body.innerHTML = `${head}<div class="card"><h3>Сейчас тихо</h3><p>Крупных транзитов к вашей карте нет — спокойный период.</p></div>`;
          bindProfileBack();
          return;
        }

        body.innerHTML = `${head}
          <div class="card">
            ${data.transits
              .map(
                (t) => `<p style="margin:0 0 10px"><b style="color:var(--gold)">${esc(t.title)}</b><br>
                        <span class="note">${esc(t.timing)}</span></p>`
              )
              .join("")}
          </div>
          <div class="card" id="p-text"><p class="waiting">Пишу разбор…</p></div>`;
        bindProfileBack();

        const slot = $("#p-text");
        const written = await api("forecast", { method: "POST" });
        slot.innerHTML = written.text
          ? `<div class="reading">${written.text}</div>`
          : "<p>Разбор к транзитам сейчас недоступен — модель не ответила.</p>";
      } catch (err) {
        fail(body, err);
      }
    },
  };

  /* Приглашения и промокод на одном экране: это один разговор о том, как
     получить расклады не за деньги. Ссылка сверху, код снизу. */
  profileRender.invite = async function inviteView() {
    const body = $("#profile-body");
    const head = `${backLink("В кабинет", "p-back")}
      <h3 style="font-family:var(--serif);font-weight:500;font-size:22px;margin:0 0 10px">Пригласить друга</h3>`;
    body.innerHTML = `${head}<p class="waiting">Готовлю ссылку…</p>`;
    bindProfileBack();

    try {
      const data = await api("invite");
      const forYou = `${data.bonus} ${plural(data.bonus, "расклад", "расклада", "раскладов")}`;
      const forFriend = `${data.welcome} ${plural(data.welcome, "расклад", "расклада", "раскладов")}`;

      body.innerHTML = `${head}
        <div class="card">
          <h3>Зовите своих</h3>
          <p>Кто придёт по вашей ссылке и сделает первый расклад — принесёт вам ${forYou}.
             Другу при входе достаётся ${forFriend}.</p>
        </div>
        <div class="field one-line plain link" id="i-link">${esc(data.link)}</div>
        <button class="btn" id="i-share">Отправить другу</button>
        <button class="btn ghost" id="i-copy">Скопировать ссылку</button>
        <div class="rows" style="margin:16px 0 18px">
          <div class="row"><span>Приглашено</span><b id="i-count">${data.invited}</b></div>
          <div class="row"><span>Бесплатных раскладов</span>
            <b id="i-left">${data.unlimited ? "без ограничений" : data.free_left}</b></div>
        </div>
        <h3 style="font-family:var(--serif);font-weight:500;font-size:20px;margin:0 0 8px">Промокод</h3>
        <p class="lead">Если у вас есть код — введите, расклады начислятся сразу.</p>
        <input class="field one-line plain" id="i-code" placeholder="ОСЕНЬ"
               autocapitalize="characters" autocomplete="off" spellcheck="false">
        <button class="btn ghost" id="i-apply">Применить</button>
        <p class="note" id="i-msg"></p>`;
      bindProfileBack();

      const share = () => {
        tap("medium");
        const text =
          "Расклады Таро с разбором — заходи по моей ссылке, первый расклад уже ждёт.";
        const url = `https://t.me/share/url?url=${encodeURIComponent(data.link)}&text=${encodeURIComponent(text)}`;
        if (tg && tg.openTelegramLink) tg.openTelegramLink(url);
        else window.open(url, "_blank");
      };
      $("#i-share").addEventListener("click", share);

      $("#i-copy").addEventListener("click", async () => {
        tap();
        const note = $("#i-msg");
        try {
          await navigator.clipboard.writeText(data.link);
          note.textContent = "Ссылка скопирована.";
        } catch (err) {
          // В некоторых клиентах буфер обмена закрыт — честнее сказать, чем
          // делать вид, что скопировали
          note.textContent = "Скопировать не вышло — выделите ссылку выше вручную.";
        }
      });

      $("#i-apply").addEventListener("click", async () => {
        const field = $("#i-code");
        const button = $("#i-apply");
        const note = $("#i-msg");
        const code = (field.value || "").trim();
        if (!code) {
          note.textContent = "Введите код.";
          return;
        }
        button.disabled = true;
        note.textContent = "";
        try {
          const result = await api("promo", {
            method: "POST",
            body: JSON.stringify({ code }),
          });
          tap("medium");
          field.value = "";
          note.textContent = `Принято: +${result.readings} ${plural(
            result.readings,
            "расклад",
            "расклада",
            "раскладов"
          )}.`;
          const left = $("#i-left");
          if (left && !data.unlimited) left.textContent = result.free_left;
        } catch (err) {
          note.textContent = err.message;
        }
        button.disabled = false;
      });
    } catch (err) {
      fail(body, err);
    }
  };

  /* Хозяйский экран: список кодов и форма нового. Кнопка спрятана от клиента,
     но решает не вёрстка — сервер проверяет сам и чужому отвечает отказом. */
  /* Сохранённый расклад: те же карты и тот самый разбор. Пишется он один раз,
     и перечитать его человек вправе — платить второй раз за написанное нет. */
  profileRender.reading = async function readingView(id) {
    const body = $("#profile-body");
    const head = `${backLink("В кабинет", "p-back")}`;
    body.innerHTML = `${head}<p class="waiting">Достаю расклад…</p>`;
    bindProfileBack();

    try {
      const data = await api(`readings/${encodeURIComponent(id)}`);
      const wide = data.cards.length > 5;
      const laid = data.cards.length
        ? `<div class="deal${wide ? " wide" : ""}">
             ${data.cards
               .map(
                 (card) => `
               <div class="slot${card.reversed ? " rev" : ""}">
                 <img src="${esc(card.image)}" alt="${esc(card.name)}" loading="lazy">
                 <b>${esc(card.name)}</b>
                 <span>${esc(card.position)}</span>
               </div>`
               )
               .join("")}
           </div>`
        : "";

      body.innerHTML = `${head}
        <h3 style="font-family:var(--serif);font-weight:500;font-size:22px;margin:0 0 4px">${esc(data.title)}</h3>
        <p class="lead">${esc(dateLabel(data.created_at))}${data.question ? ` · «${esc(data.question)}»` : ""}</p>
        ${laid}
        <div class="card"><div class="reading">${data.text || "Разбор к этому раскладу не сохранился."}</div></div>
        ${data.ai ? "" : '<p class="note">Разбор собран по значениям карт: модель тогда была недоступна.</p>'}`;
      bindProfileBack();
    } catch (err) {
      fail(body, err);
    }
  };

  profileRender.promos = async function promosView() {
    const body = $("#profile-body");
    const head = `${backLink("В кабинет", "p-back")}
      <h3 style="font-family:var(--serif);font-weight:500;font-size:22px;margin:0 0 10px">Промокоды</h3>`;
    body.innerHTML = `${head}<p class="waiting">Смотрю, что уже заведено…</p>`;
    bindProfileBack();

    const line = (item) => {
      const taken = item.uses_limit ? `${item.used} из ${item.uses_limit}` : `${item.used}`;
      const until = item.expires_at ? ` · до ${dateLabel(item.expires_at)}` : "";
      return `
        <div class="card${item.active ? "" : " off"}">
          <h3>${esc(item.code)} — ${item.readings} ${plural(item.readings, "расклад", "расклада", "раскладов")}</h3>
          <p>Взяли: ${taken}${until}${item.active ? "" : " · выключен"}</p>
          <button class="btn ghost small" data-toggle="${item.id}" data-active="${item.active ? 1 : 0}">
            ${item.active ? "Выключить" : "Включить"}
          </button>
        </div>`;
    };

    const draw = (codes) => {
      body.innerHTML = `${head}
        <div id="pr-list">
          ${codes.length ? codes.map(line).join("") : '<p class="lead">Кодов пока нет.</p>'}
        </div>

        <h3 style="font-family:var(--serif);font-weight:500;font-size:20px;margin:18px 0 8px">Новый код</h3>
        <input class="field one-line plain" id="pr-code" placeholder="ОСЕНЬ"
               autocapitalize="characters" autocomplete="off" spellcheck="false">
        <label class="note">Сколько раскладов даёт</label>
        <input class="field one-line plain" id="pr-readings" inputmode="numeric" value="3">
        <label class="note">Сколько раз можно взять — пусто значит без ограничения</label>
        <input class="field one-line plain" id="pr-limit" inputmode="numeric" placeholder="100">
        <label class="note">Действует до — пусто значит бессрочно</label>
        <input class="field one-line plain" id="pr-until" placeholder="2027-01-31">
        <button class="btn" id="pr-add">Завести код</button>
        <p class="note" id="pr-msg"></p>`;
      bindProfileBack();

      body.querySelectorAll("[data-toggle]").forEach((button) =>
        button.addEventListener("click", async () => {
          tap();
          button.disabled = true;
          try {
            await api(`admin/promos/${button.dataset.toggle}/active`, {
              method: "POST",
              body: JSON.stringify({ active: button.dataset.active !== "1" }),
            });
            draw((await api("admin/promos")).codes);
          } catch (err) {
            $("#pr-msg").textContent = err.message;
            button.disabled = false;
          }
        })
      );

      $("#pr-add").addEventListener("click", async () => {
        const button = $("#pr-add");
        const note = $("#pr-msg");
        button.disabled = true;
        note.textContent = "";
        try {
          await api("admin/promos", {
            method: "POST",
            body: JSON.stringify({
              code: ($("#pr-code").value || "").trim(),
              readings: Number($("#pr-readings").value || 0),
              uses_limit: Number($("#pr-limit").value || 0),
              expires_at: ($("#pr-until").value || "").trim(),
            }),
          });
          tap("medium");
          draw((await api("admin/promos")).codes);
          $("#pr-msg").textContent = "Код заведён.";
        } catch (err) {
          note.textContent = err.message;
          button.disabled = false;
        }
      });
    };

    try {
      draw((await api("admin/promos")).codes);
    } catch (err) {
      fail(body, err);
    }
  };

  profileRender.natal = async function natalView() {
    const body = $("#profile-body");
    const head = `${backLink("В кабинет", "p-back")}
      <h3 style="font-family:var(--serif);font-weight:500;font-size:22px;margin:0 0 10px">Подробный разбор карты</h3>`;
    body.innerHTML = `${head}<p class="waiting">Смотрю, что уже есть…</p>`;
    bindProfileBack();

    try {
      const data = await api("natal");
      if (!data.ready) {
        body.innerHTML = `${head}<div class="card"><h3>Нужны время и город рождения</h3>
          <p>Без них не считаются дома, а разбор по домам — половина смысла. Заполните данные в кабинете.</p></div>`;
        bindProfileBack();
        return;
      }
      if (data.written) {
        body.innerHTML = `${head}<div class="card"><div class="reading">${data.text}</div></div>`;
        bindProfileBack();
        return;
      }

      body.innerHTML = `${head}
        <div class="card">
          <h3>Разбор на весь ваш круг</h3>
          <p>Солнце, Луна, асцендент, дома и аспекты — связно, одним текстом. Пишется один раз: карта рождения не меняется.</p>
        </div>
        <button class="btn" id="n-go">${data.paid ? "Написать разбор" : `Заказать за ${data.price} ${esc(data.currency)}`}</button>
        <p class="note" id="n-msg"></p>`;
      bindProfileBack();

      $("#n-go").addEventListener("click", async () => {
        const button = $("#n-go");
        button.disabled = true;
        tap("medium");
        if (!data.paid) {
          try {
            const invoice = await api("invoice", {
              method: "POST",
              body: JSON.stringify({ kind: "natal" }),
            });
            if (!tg || !tg.openInvoice) {
              $("#n-msg").textContent = "Оплата открывается только внутри Telegram.";
              button.disabled = false;
              return;
            }
            tg.openInvoice(invoice.link, (status) => {
              if (status === "paid") {
                profileView("natal");
                return;
              }
              $("#n-msg").textContent =
                status === "failed" ? "Оплата не прошла." : "Оплата отменена.";
              button.disabled = false;
            });
          } catch (err) {
            $("#n-msg").textContent = err.message;
            button.disabled = false;
          }
          return;
        }

        $("#n-msg").textContent = "";
        button.insertAdjacentHTML("afterend", '<p class="waiting" id="n-wait">Пишу разбор…</p>');
        button.remove();
        try {
          const done = await api("natal", { method: "POST" });
          body.innerHTML = `${head}<div class="card"><div class="reading">${done.text}</div></div>`;
          bindProfileBack();
        } catch (err) {
          const wait = $("#n-wait");
          if (wait) wait.outerHTML = `<p class="note">${esc(err.message)}</p>`;
        }
      });
    } catch (err) {
      fail(body, err);
    }
  };

  let chosenPlace = null;

  function bindProfileBack() {
    const back = $("#p-back");
    if (back) {
      back.addEventListener("click", () => {
        tap();
        profileView("main");
      });
    }
  }

  async function showNamesakes(query) {
    const box = $("#p-cities");
    const data = await api(`cities?q=${encodeURIComponent(query)}`);
    box.innerHTML = data.cities
      .map(
        (city) =>
          `<button class="chip" data-place="${city.id}">${esc(city.name)} · ${esc(city.hint)}</button>`
      )
      .join("");
    box.querySelectorAll("[data-place]").forEach((node) =>
      node.addEventListener("click", () => {
        tap();
        chosenPlace = Number(node.dataset.place);
        box.querySelectorAll("[data-place]").forEach((other) => other.classList.remove("on"));
        node.classList.add("on");
      })
    );
  }

  const isoToRu = (iso) => {
    if (!iso) return "";
    const [y, m, d] = iso.split("-");
    return `${d}.${m}.${y}`;
  };

  async function profileView(view = "main", me = null) {
    chosenPlace = view === "edit" ? chosenPlace : null;
    await profileRender[view](me);
  }

  /* --- расклад: раскладка → вопрос → число → карты → разбор ---------------
     Четыре шага живут в одном экране, потому что это один разговор: уйти
     на вкладку и вернуться значит начать заново. */

  let flow = null;

  const backLink = (label, id = "flow-back") =>
    `<button class="back-link" id="${id}">
       <svg><use href="assets/icons.svg#i-back"></use></svg>${esc(label)}
     </button>`;

  async function spreadList() {
    const body = $("#spread-body");
    flow = null;
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
          const spread = data.spreads.find((s) => s.key === node.dataset.spread);
          askQuestion(spread);
        })
      );
    } catch (err) {
      fail(body, err);
    }
  }

  function askQuestion(spread) {
    const body = $("#spread-body");
    flow = { spread, question: null, number: null };
    body.innerHTML = `
      ${backLink("К раскладкам")}
      <h3 style="font-family:var(--serif);font-weight:500;font-size:22px;margin:0 0 4px">${esc(spread.title)}</h3>
      <p class="lead">${esc(spread.subtitle)}</p>
      <textarea class="field" id="flow-q" maxlength="500"
        placeholder="${spread.yes_no ? "Вопрос, на который можно ответить «да» или «нет»" : "О чём спрашиваете? Чем точнее вопрос, тем точнее разбор"}"></textarea>
      <button class="btn" id="flow-next">Дальше</button>
      ${spread.yes_no ? "" : '<button class="btn ghost" id="flow-skip" style="margin-top:10px">Без вопроса</button>'}`;

    $("#flow-back").addEventListener("click", () => {
      tap();
      spreadList();
    });
    $("#flow-next").addEventListener("click", () => {
      const value = $("#flow-q").value.trim();
      if (spread.yes_no && !value) {
        $("#flow-q").focus();
        return;
      }
      tap();
      askNumber(value || null);
    });
    const skip = $("#flow-skip");
    if (skip) {
      skip.addEventListener("click", () => {
        tap();
        askNumber(null);
      });
    }
  }

  function askNumber(question) {
    const body = $("#spread-body");
    flow.question = question;
    body.innerHTML = `
      ${backLink("К вопросу")}
      <h3 style="font-family:var(--serif);font-weight:500;font-size:22px;margin:0 0 4px">Назовите число</h3>
      <p class="lead">От 1 до 99. Карты лягут под него — это ваш жест, а не случайность сервера.</p>
      <input class="field one-line" id="flow-n" type="number" inputmode="numeric" min="1" max="99" placeholder="—">
      <button class="btn" id="flow-go">Разложить</button>
      <button class="btn ghost" id="flow-any" style="margin-top:10px">Пусть ляжет само</button>`;

    $("#flow-back").addEventListener("click", () => {
      tap();
      askQuestion(flow.spread);
    });
    $("#flow-go").addEventListener("click", () => {
      const value = parseInt($("#flow-n").value, 10);
      tap("medium");
      deal(Number.isFinite(value) ? value : 0);
    });
    $("#flow-any").addEventListener("click", () => {
      tap("medium");
      deal(0);
    });
  }

  async function deal(number) {
    const body = $("#spread-body");
    // Запоминаем: если расклада не дали из-за подписки, после неё карты
    // должны лечь под то же число, которое человек назвал
    flow.number = number;
    body.innerHTML = `<p class="waiting">Тасую колоду…</p>`;
    try {
      const data = await api("reading", {
        method: "POST",
        body: JSON.stringify({
          spread: flow.spread.key,
          question: flow.question,
          number,
        }),
      });

      if (!data.allowed) {
        body.innerHTML = refusal(data);
        bindRefusal();
        return;
      }

      const wide = data.cards.length > 5;
      body.innerHTML = `
        ${backLink("К раскладкам")}
        <h3 style="font-family:var(--serif);font-weight:500;font-size:22px;margin:0 0 4px">${esc(data.spread.title)}</h3>
        ${data.question ? `<p class="lead">«${esc(data.question)}»</p>` : ""}
        <div class="deal${wide ? " wide" : ""}">
          ${data.cards
            .map(
              (card, index) => `
            <div class="slot${card.reversed ? " rev" : ""}">
              <img src="${esc(card.image)}" alt="${esc(card.name)}" loading="lazy"
                   style="animation-delay:${index * 90}ms">
              <b>${esc(card.name)}</b>
              <span>${esc(card.position)}</span>
            </div>`
            )
            .join("")}
        </div>
        <div class="card" id="flow-reading"><p class="waiting">Пишу разбор…</p></div>`;

      $("#flow-back").addEventListener("click", () => {
        tap();
        spreadList();
      });

      // Разбор идёт отдельным запросом: модель пишет до минуты, а карты
      // человек должен увидеть сразу
      const slot = $("#flow-reading");
      try {
        const done = await api(`reading/${encodeURIComponent(data.token)}/interpretation`, {
          method: "POST",
        });
        slot.innerHTML = `<div class="reading">${done.text}</div>`;
        if (!done.ai) {
          slot.insertAdjacentHTML(
            "beforeend",
            '<p class="note">Разбор собран по значениям карт: модель сейчас недоступна.</p>'
          );
        }
        if (typeof done.free_left === "number") {
          slot.insertAdjacentHTML(
            "beforeend",
            `<p class="note">Бесплатных раскладов осталось: ${done.free_left}</p>`
          );
        }
        loaded.delete("profile");
      } catch (err) {
        slot.innerHTML = `<p>Разбор не дописался: ${esc(err.message)}</p>`;
      }
    } catch (err) {
      fail(body, err);
    }
  }

  function refusal(data) {
    const texts = {
      needs_daily: {
        title: "Включите карту дня",
        body: "Бесплатные расклады открыты тем, кто получает утреннюю карту. Это одно нажатие, отключить можно в любой момент.",
        action: "Включить утреннюю карту",
      },
      needs_payment: {
        title: "Бесплатные закончились",
        body: "Вы использовали все бесплатные расклады. Следующий — платный.",
        action: "Оплатить расклад",
        pay: true,
      },
      payment_not_ready: {
        title: "Оплата пока не подключена",
        body: "Бесплатные расклады закончились, а приём оплаты автор ещё не настроил. Напишите ему напрямую.",
        action: null,
      },
      daily_limit: {
        title: "На сегодня хватит",
        body: `Больше ${data.limit} раскладов в день не делаем: карты любят паузу.`,
        action: null,
      },
    };
    const item = texts[data.reason] || {
      title: "Расклад недоступен",
      body: "Попробуйте позже.",
      action: null,
    };
    return `
      ${backLink("К раскладкам")}
      <div class="card">
        <h3>${esc(item.title)}</h3>
        <p>${esc(item.body)}</p>
      </div>
      ${item.action ? `<button class="btn" id="flow-fix" data-pay="${item.pay ? 1 : 0}">${esc(item.action)}</button>` : ""}
      <p class="note" id="flow-msg"></p>`;
  }

  function bindRefusal() {
    $("#flow-back").addEventListener("click", () => {
      tap();
      spreadList();
    });
    const fix = $("#flow-fix");
    if (!fix) return;
    fix.addEventListener("click", async () => {
      fix.disabled = true;
      tap();
      try {
        if (fix.dataset.pay === "1") {
          await payAndDeal();
          return;
        }
        await api("daily/subscription", { method: "POST" });
        loaded.delete("daily");
        loaded.delete("profile");
        deal(flow.number || 0);
      } catch (err) {
        $("#flow-msg").textContent = err.message;
        fix.disabled = false;
      }
    });
  }

  async function payAndDeal() {
    const invoice = await api("invoice", {
      method: "POST",
      body: JSON.stringify({ kind: "auto" }),
    });
    if (!tg || !tg.openInvoice) {
      $("#flow-msg").textContent = "Оплата открывается только внутри Telegram.";
      $("#flow-fix").disabled = false;
      return;
    }
    // Телеграм сам показывает окно оплаты и возвращает исход; деньги
    // записывает бот, когда придёт successful_payment
    tg.openInvoice(invoice.link, (status) => {
      if (status === "paid") {
        loaded.delete("profile");
        deal(flow.number || 0);
        return;
      }
      $("#flow-msg").textContent =
        status === "failed" ? "Оплата не прошла." : "Оплата отменена.";
      $("#flow-fix").disabled = false;
    });
  }

  /* --- авторский разбор: уровень → вопрос → заявка ------------------------
     Здесь человек пишет живому тарологу, а не модели, поэтому и шагов
     меньше: выбрать глубину, сформулировать вопрос, оставить связь. */

  async function authorView() {
    const body = $("#author-body");
    try {
      const data = config || (await loadConfig());
      $("#author-lead").textContent = `Разбор делает ${data.author} — живой человек, а не модель.`;

      const me = await api("me");
      if (me.open_order) {
        authorSent(me.open_order, data);
        await appendOrders(body, me.open_order.id);
        return;
      }

      if (!data.tiers.length) {
        body.innerHTML = `<div class="card"><h3>Пока закрыто</h3><p>Автор не принимает заявки.</p></div>`;
        return;
      }

      body.innerHTML = data.tiers
        .map(
          (tier) => `
          <div class="card tap" data-tier="${esc(tier.key)}">
            <h3>${esc(tier.title)} · ${tier.price} ${esc(data.currency)}</h3>
            <p>${esc(tier.summary)}</p>
            <ul class="note" style="margin:10px 0 0;padding-left:18px">
              ${tier.includes.map((line) => `<li>${esc(line)}</li>`).join("")}
            </ul>
            <p class="note">ответ ${esc(tier.hours)} ч · уточнений: ${tier.followups}</p>
          </div>`
        )
        .join("");

      body.querySelectorAll("[data-tier]").forEach((node) =>
        node.addEventListener("click", () => {
          tap();
          authorForm(data.tiers.find((t) => t.key === node.dataset.tier), data);
        })
      );

      await appendOrders(body);
    } catch (err) {
      fail(body, err);
    }
  }

  /* Прошлые заявки: ответ таролога пишется один раз и остаётся — читать его
     человек приходит сюда, а не листает переписку. */
  async function appendOrders(body, skipId) {
    let orders = [];
    try {
      orders = (await api("orders")).orders || [];
    } catch (err) {
      return; // заявки — дополнение к экрану, а не сам экран
    }
    const shown = orders.filter((order) => order.id !== skipId);
    if (!shown.length) return;

    body.insertAdjacentHTML(
      "beforeend",
      `<h3 style="font-family:var(--serif);font-weight:500;margin:20px 2px 10px">Ваши заявки</h3>
       <div class="rows">
         ${shown
           .map(
             (order) => `
           <button class="row tap" data-order="${order.id}">
             <span>№${order.id} · ${esc(order.tier_title || "")}</span>
             <b>${esc(order.label || "")} ›</b>
           </button>`
           )
           .join("")}
       </div>`
    );

    body.querySelectorAll("[data-order]").forEach((node) =>
      node.addEventListener("click", () => {
        tap();
        authorThread(node.dataset.order);
      })
    );
  }

  /* Заявка целиком: вопрос, ответ и уточнения — по порядку, как разговор. */
  async function authorThread(id) {
    const body = $("#author-body");
    const head = `${backLink("К разборам", "a-back")}`;
    body.innerHTML = `${head}<p class="waiting">Открываю заявку…</p>`;
    $("#a-back").addEventListener("click", () => {
      tap();
      authorView();
    });

    try {
      const order = await api(`orders/${encodeURIComponent(id)}`);
      const said = (item) =>
        item.text
          ? `<div class="card${item.author === "client" ? " mine" : ""}">
               <p class="note">${item.author === "client" ? "Ваше уточнение" : "Ответ"} · ${esc(dateLabel(item.created_at))}</p>
               <div class="reading">${item.text}</div>
             </div>`
          : `<div class="card"><p class="note">Ответ пришёл в чат — он не текстом.</p></div>`;

      const left = order.followups.left;
      body.innerHTML = `${head}
        <h3 style="font-family:var(--serif);font-weight:500;font-size:22px;margin:0 0 4px">Заявка №${order.id}</h3>
        <p class="lead">${esc(order.tier_title || "")} · ${esc(order.label || "")} · ${esc(dateLabel(order.created_at))}</p>
        <div class="card mine">
          <p class="note">Ваш вопрос</p>
          <div class="reading">${esc(order.question || "")}</div>
        </div>
        ${order.thread.map(said).join("")}
        ${
          order.status === "answered"
            ? left > 0
              ? `<label class="note">Уточняющий вопрос — осталось ${left}</label>
                 <textarea class="field" id="a-more" maxlength="500"
                   placeholder="Спросите о том, что осталось непонятным"></textarea>
                 <button class="btn" id="a-ask">Отправить уточнение</button>
                 <p class="note" id="a-msg"></p>`
              : '<p class="note">Уточнения по этой заявке кончились.</p>'
            : '<p class="note">Ответ придёт сюда и дублем в чат.</p>'
        }`;
      $("#a-back").addEventListener("click", () => {
        tap();
        authorView();
      });

      const ask = $("#a-ask");
      if (!ask) return;
      ask.addEventListener("click", async () => {
        const field = $("#a-more");
        const note = $("#a-msg");
        const question = (field.value || "").trim();
        if (!question) {
          note.textContent = "Напишите вопрос.";
          return;
        }
        ask.disabled = true;
        note.textContent = "";
        try {
          await api(`orders/${order.id}/followup`, {
            method: "POST",
            body: JSON.stringify({ text: question }),
          });
          tap("medium");
          authorThread(order.id);
        } catch (err) {
          note.textContent = err.message;
          ask.disabled = false;
        }
      });
    } catch (err) {
      fail(body, err);
    }
  }

  function authorForm(tier, data) {
    const body = $("#author-body");
    body.innerHTML = `
      ${backLink("К уровням", "a-back")}
      <h3 style="font-family:var(--serif);font-weight:500;font-size:22px;margin:0 0 4px">${esc(tier.title)} · ${tier.price} ${esc(data.currency)}</h3>
      <p class="lead">${esc(tier.summary)}. Ответ ${esc(tier.hours)} ч.</p>
      <label class="note">Ваш вопрос</label>
      <textarea class="field" id="a-q" maxlength="500"
        placeholder="Опишите ситуацию: что происходит и что хотите понять"></textarea>
      <label class="note">Как с вами связаться, если понадобится</label>
      <input class="field one-line plain" id="a-contact" placeholder="@ник или телефон — необязательно">
      <button class="btn" id="a-send">Отправить заявку</button>
      <p class="note" id="a-msg"></p>`;

    $("#a-back").addEventListener("click", () => {
      tap();
      authorView();
    });

    $("#a-send").addEventListener("click", async () => {
      const button = $("#a-send");
      const question = $("#a-q").value.trim();
      if (!question) {
        $("#a-q").focus();
        return;
      }
      button.disabled = true;
      tap("medium");
      try {
        const sent = await api("order", {
          method: "POST",
          body: JSON.stringify({
            tier: tier.key,
            question,
            contact: $("#a-contact").value.trim() || null,
          }),
        });
        loaded.delete("profile");
        if (sent.created) {
          authorSent({ id: sent.order.id, tier: sent.order.tier }, data, sent.hours);
        } else {
          authorSent(sent.order, data);
        }
      } catch (err) {
        $("#a-msg").textContent = err.message;
        button.disabled = false;
      }
    });
  }

  function authorSent(order, data, hours) {
    const body = $("#author-body");
    const tier = (data.tiers || []).find((t) => t.key === order.tier);
    const deadline = hours || (tier && tier.hours);
    body.innerHTML = `
      <div class="card">
        <h3>Заявка №${order.id} у автора</h3>
        <p>${esc(data.author)} ответит${deadline ? ` в течение ${deadline} ч` : ""}. Ответ придёт сюда и дублем в чат.</p>
        ${order.created_at ? `<p class="note">Отправлена ${esc(dateLabel(order.created_at))}</p>` : ""}
      </div>
      <button class="btn ghost" id="a-cancel">Отменить заявку</button>
      <p class="note" id="a-msg"></p>`;

    $("#a-cancel").addEventListener("click", async () => {
      const button = $("#a-cancel");
      button.disabled = true;
      tap();
      try {
        await api(`order/${order.id}`, { method: "DELETE" });
        loaded.delete("profile");
        authorView();
      } catch (err) {
        $("#a-msg").textContent = err.message;
        button.disabled = false;
      }
    });
  }

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

  // Системная кнопка «назад» принадлежит развороту карты, пока он открыт:
  // внутри Telegram это первое, что нажимают, и закрывать приложение она
  // в этот момент не должна.
  if (window.DeckScreen && tg && tg.BackButton) {
    window.DeckScreen.onViewerOpen = () => {
      tg.BackButton.show();
    };
    window.DeckScreen.onViewerClose = () => {
      tg.BackButton.hide();
    };
    tg.BackButton.onClick(() => {
      if (window.DeckScreen.isOpen()) window.DeckScreen.close();
    });
  }

  function startScreen() {
    // Бот передаёт экран якорем (#spread) или параметром запуска: человек
    // набрал знакомую команду и должен попасть сразу куда хотел
    const names = new Set(["daily", "spread", "author", "profile", "deck"]);
    const hash = (location.hash || "").replace("#", "").trim();
    if (names.has(hash)) return hash;
    const param = tg && tg.initDataUnsafe ? tg.initDataUnsafe.start_param : "";
    return names.has(param) ? param : "daily";
  }

  loadConfig().catch(() => {});
  show(startScreen());
})();
