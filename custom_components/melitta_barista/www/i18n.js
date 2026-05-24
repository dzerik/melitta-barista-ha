/**
 * Panel i18n.
 *
 * Strategy: bundle all panel strings in this module. HA's
 * `frontend/get_translations` doesn't expose a "panel" category, so for the
 * SPA we maintain our own dictionary. English is the source of truth; other
 * locales fall back to English when a key is missing.
 *
 * Adding a new language: add a sibling object to STRINGS keyed by HA language
 * code (`hass.language`). Untranslated keys silently fall back to `en`.
 */

const STRINGS = {
  en: {
    "panel.title": "Melitta Barista",
    "panel.no_entries": "No coffee machines configured for this integration.",

    "tabs.status": "Status",
    "tabs.diagnostics": "Diagnostics",
    "tabs.recipes": "Recipes",
    "tabs.beans": "Beans",
    "tabs.additives": "Add-ins",
    "tabs.sommelier": "Sommelier",
    "tabs.settings": "Settings",

    "common.loading": "Loading…",
    "common.error": "Error",
    "common.save": "Save",
    "common.cancel": "Cancel",
    "common.delete": "Delete",
    "common.delete_confirm": "Delete this item?",
    "common.add": "Add",
    "common.edit": "Edit",
    "common.refresh": "Refresh",
    "common.yes": "Yes",
    "common.no": "No",
    "common.unknown": "Unknown",
    "common.never": "Never",
    "common.empty": "Empty",
    "common.confirm": "Confirm",
    "confirm.delete.title": "Delete item",
    "confirm.delete.confirm": "Delete",

    "status.title": "Machine status",
    "status.ble_state": "BLE connection",
    "status.connected": "Connected",
    "status.disconnected": "Disconnected",
    "status.machine_state": "Machine state",
    "status.process": "Process",
    "status.manipulation": "Prompt",
    "status.firmware": "Firmware",
    "status.model": "Model",
    "status.family": "Family",
    "status.slots": "MyCoffee slots",
    "status.machine_type": "Machine type",
    "status.dis": "Device information",
    "status.cup_total": "Total cups",
    "status.cup_by_recipe": "Cups by recipe",
    "status.no_status": "No status received yet.",
    "status.last_update": "Last status update",
    "status.profile_active": "Active profile",
    "status.selected_recipe": "Selected recipe",

    "diag.title": "Diagnostics",
    "diag.recent_errors": "Recent errors",
    "diag.recent_frames": "Recent BLE frames",
    "diag.no_errors": "No errors recorded.",
    "diag.no_frames": "No frames recorded yet.",
    "diag.address": "BLE address",
    "diag.brand": "Brand",
    "diag.proxy": "Transport",
    "diag.proxy_local": "Local BlueZ adapter",
    "diag.proxy_remote": "ESPHome BLE proxy",
    "diag.poll_interval": "Polling interval",
    "diag.handshake": "Last handshake",
    "diag.clear": "Clear log",

    "recipes.title": "Recipes & DirectKey",
    "recipes.base_recipes": "Base recipes",
    "recipes.directkey": "DirectKey profiles",
    "recipes.id": "ID",
    "recipes.name": "Name",
    "recipes.process": "Process",
    "recipes.intensity": "Intensity",
    "recipes.aroma": "Aroma",
    "recipes.portion": "Portion (ml)",
    "recipes.temperature": "Temperature",
    "recipes.shots": "Shots",
    "recipes.profile": "Profile",
    "recipes.category": "Category",
    "recipes.reset_default": "Reset to default",
    "recipes.coming_soon": "Recipe editor — coming in the next iteration.",

    "beans.title": "Coffee beans & producers",
    "beans.producers": "Producers",
    "beans.beans": "Beans",
    "beans.add_producer": "Add producer",
    "beans.add_bean": "Add bean",
    "beans.producer_name": "Producer",
    "beans.bean_name": "Name",
    "beans.roast": "Roast",
    "beans.origin": "Origin",
    "beans.varietal": "Varietal",
    "beans.notes": "Notes",
    "beans.recommended_brewing": "Recommended brewing",
    "beans.brewing_label": "Brewing",
    "beans.hopper": "Hopper",
    "beans.hopper_left": "Left hopper",
    "beans.hopper_right": "Right hopper",
    "beans.autofill": "Fill from LLM",
    "beans.autofill_running": "Asking the assistant…",
    "beans.no_beans": "No beans added yet.",
    "beans.no_producers": "No producers yet — add one first.",
    "beans.hopper.assigned": "✓ Hopper {hopper}: {bean}",
    "beans.hopper.mismatch": "Save not confirmed: WS returned OK, but hopper {hopper} contains \"{actual}\" instead of \"{expected}\" after reload. Check HA logs.",
    "beans.hopper.failed": "Assign to hopper {hopper} failed: {error}",

    "additives.title": "Add-ins",
    "additives.syrups": "Syrups",
    "additives.toppings": "Toppings",
    "additives.milk": "Milk types",
    "additives.add": "Add",
    "additives.name": "Name",
    "additives.brand": "Brand",
    "additives.notes": "Notes",
    "additives.empty_syrups": "No syrups configured.",
    "additives.empty_toppings": "No toppings configured.",
    "additives.empty_milk": "No milk types configured.",

    "sommelier.title": "AI Sommelier",
    "sommelier.prompt": "Describe what you'd like to drink",
    "sommelier.generate": "Generate",
    "sommelier.brew_this": "Brew this",
    "sommelier.context_beans": "Beans in hopper",
    "sommelier.context_milk": "Milk available",
    "sommelier.context_extras": "Add-ins available",
    "sommelier.last_recipe": "Latest recipe",
    "sommelier.no_recipe": "Ask the sommelier to design a drink.",
    "sommelier.brewing": "Sending brew command…",
    "sommelier.brew_ok": "Brew started.",
    "sommelier.brew_failed": "Brew failed.",
    "sommelier.constraints_heading": "Constraints and mood",
    "sommelier.cup_size": "Cup size",
    "sommelier.mood_label": "Mood (multi-select)",
    "sommelier.occasion_label": "Occasion (suggested from time of day)",
    "sommelier.temperature_label": "Temperature",
    "sommelier.caffeine_label": "Caffeine",
    "sommelier.dietary_label": "Dietary (multi-select)",
    "sommelier.addins_heading": "Available add-ins (multi-select)",
    "sommelier.section_syrups": "Syrups",
    "sommelier.section_toppings": "Toppings",
    "sommelier.section_milk": "Milk",
    "sommelier.addin_unconfigured": "— not configured under Add-ins",
    "sommelier.machine_label": "Machine:",
    "sommelier.why": "Why?",
    "sommelier.favorited_toast": "★ Added to favorites",
    "sommelier.fav_in": "Favorited",
    "sommelier.fav_add": "Add to favorites",
    "sommelier.favorite_failed": "Failed to add to favorites",
    "sommelier.addins_load_failed": "Failed to load add-ins",

    "sommelier.cup.espresso_cup": "Espresso cup",
    "sommelier.cup.cup": "Cup",
    "sommelier.cup.mug": "Mug",
    "sommelier.cup.tall_glass": "Tall glass",
    "sommelier.cup.travel": "Travel mug",

    "sommelier.mood.energizing": "Energizing",
    "sommelier.mood.relaxing": "Relaxing",
    "sommelier.mood.dessert": "Dessert",
    "sommelier.mood.classic": "Classic",

    "sommelier.occasion.morning": "Morning",
    "sommelier.occasion.after_lunch": "After lunch",
    "sommelier.occasion.guests": "Guests",
    "sommelier.occasion.romantic": "Romantic",
    "sommelier.occasion.work": "Work",

    "sommelier.temp.auto": "Auto",
    "sommelier.temp.hot": "Hot",
    "sommelier.temp.iced": "Iced",

    "sommelier.caffeine.regular": "Regular",
    "sommelier.caffeine.low": "Low",
    "sommelier.caffeine.decaf_evening": "Decaf (evening)",

    "sommelier.diet.no_sugar": "No sugar",
    "sommelier.diet.lactose_free": "Lactose-free",
    "sommelier.diet.low_calorie": "Low calorie",
    "sommelier.diet.vegan": "Vegan",

    "modal.add_bean": "Add bean",
    "modal.edit_bean": "Edit bean",
    "modal.add_producer": "Add producer",
    "modal.edit_producer": "Edit producer",
    "modal.add_additive": "Add add-in",
    "modal.edit_additive": "Edit add-in",
    "modal.type": "Type",
    "modal.type.syrup": "Syrup",
    "modal.type.topping": "Topping",
    "modal.type.milk": "Milk",

    "tags.title": "Flavor notes",
    "tags.add_placeholder": "Add tag…",
    "tags.add_button": "Add tag",

    "hopper.title": "Hopper assignment",
    "hopper.left": "Hopper 1 (left)",
    "hopper.right": "Hopper 2 (right)",
    "hopper.unassigned": "— not set —",
    "hopper.assigned": "Assigned",

    "settings.title": "Settings",
    "settings.llm_agent": "LLM model",
    "settings.llm_help": "Conversation agent used for Sommelier and bean autofill.",
    "settings.prompts": "Prompt templates",
    "settings.prompt_default": "Default",
    "settings.prompt_reset": "Reset to default",
    "settings.prompt_save": "Save",
    "settings.saved": "Saved.",
    "settings.reset_done": "Reset to default.",
    "settings.help_title": "How prompt templates work",
    "settings.help_placeholders": "Placeholders",
    "settings.help_no_placeholders": "No placeholders for this slot.",
    "settings.help_syntax": "Syntax",
    "settings.help_syntax_text":
      "Use Python str.format syntax: {name} for a value, {name!r} to wrap it in quotes. " +
      "Unknown placeholders pass through literally so you can see them in the LLM reply.",
    "settings.help_schema":
      "When a JSON Schema is shown below, it is automatically appended to your template " +
      "before the request is sent. The model is instructed to reply with strict JSON " +
      "matching that schema; replies are validated server-side and one auto-retry is " +
      "performed if validation fails.",
    "settings.help_smartchain":
      "If the SmartChain integration is installed and a SmartChain agent is selected, " +
      "the request is routed through its native structured-output API (no parsing — " +
      "the provider returns a strict object). Otherwise the text+validation path runs.",
    "settings.preview": "Preview assembled prompt",
    "settings.preview_title": "Assembled prompt",
    "settings.preview_loading": "Building preview…",

    "diag.llm_calls": "Recent LLM calls",
    "diag.no_llm_calls": "No LLM calls recorded yet.",
    "diag.llm_show_prompt": "Show prompt",
    "diag.llm_show_response": "Show response",
  },

  ru: {
    "panel.title": "Melitta Barista",
    "panel.no_entries": "Не найдено ни одной подключённой кофемашины.",

    "tabs.status": "Состояние",
    "tabs.diagnostics": "Диагностика",
    "tabs.recipes": "Рецепты",
    "tabs.beans": "Зёрна",
    "tabs.additives": "Добавки",
    "tabs.sommelier": "Сомелье",
    "tabs.settings": "Настройки",

    "common.loading": "Загрузка…",
    "common.error": "Ошибка",
    "common.save": "Сохранить",
    "common.cancel": "Отмена",
    "common.delete": "Удалить",
    "common.delete_confirm": "Удалить эту запись?",
    "common.add": "Добавить",
    "common.edit": "Изменить",
    "common.refresh": "Обновить",
    "common.yes": "Да",
    "common.no": "Нет",
    "common.unknown": "—",
    "common.never": "Никогда",
    "common.empty": "Пусто",
    "common.confirm": "Подтвердить",
    "confirm.delete.title": "Удалить элемент",
    "confirm.delete.confirm": "Удалить",

    "status.title": "Состояние машины",
    "status.ble_state": "BLE-соединение",
    "status.connected": "Подключено",
    "status.disconnected": "Отключено",
    "status.machine_state": "Состояние машины",
    "status.process": "Процесс",
    "status.manipulation": "Запрос на подтверждение",
    "status.firmware": "Прошивка",
    "status.model": "Модель",
    "status.family": "Семейство",
    "status.slots": "Слотов MyCoffee",
    "status.machine_type": "Тип машины",
    "status.dis": "Информация об устройстве",
    "status.cup_total": "Всего чашек",
    "status.cup_by_recipe": "Чашки по рецептам",
    "status.no_status": "Статус ещё не получен.",
    "status.last_update": "Последнее обновление",
    "status.profile_active": "Активный профиль",
    "status.selected_recipe": "Выбранный рецепт",

    "diag.title": "Диагностика",
    "diag.recent_errors": "Недавние ошибки",
    "diag.recent_frames": "Недавние BLE-кадры",
    "diag.no_errors": "Ошибок нет.",
    "diag.no_frames": "Кадров нет.",
    "diag.address": "BLE-адрес",
    "diag.brand": "Бренд",
    "diag.proxy": "Транспорт",
    "diag.proxy_local": "Локальный BlueZ",
    "diag.proxy_remote": "ESPHome BLE proxy",
    "diag.poll_interval": "Интервал опроса",
    "diag.handshake": "Последний handshake",
    "diag.clear": "Очистить журнал",

    "recipes.title": "Рецепты и DirectKey",
    "recipes.base_recipes": "Базовые рецепты",
    "recipes.directkey": "Профили DirectKey",
    "recipes.id": "ID",
    "recipes.name": "Название",
    "recipes.process": "Процесс",
    "recipes.intensity": "Крепость",
    "recipes.aroma": "Аромат",
    "recipes.portion": "Порция (мл)",
    "recipes.temperature": "Температура",
    "recipes.shots": "Шоты",
    "recipes.profile": "Профиль",
    "recipes.category": "Категория",
    "recipes.reset_default": "Сбросить к заводским",
    "recipes.coming_soon": "Редактор рецептов — в следующей итерации.",

    "beans.title": "Зёрна и производители",
    "beans.producers": "Производители",
    "beans.beans": "Сорта зёрен",
    "beans.add_producer": "Добавить производителя",
    "beans.add_bean": "Добавить сорт",
    "beans.producer_name": "Производитель",
    "beans.bean_name": "Название",
    "beans.roast": "Обжарка",
    "beans.origin": "Происхождение",
    "beans.varietal": "Сортотип",
    "beans.notes": "Примечания",
    "beans.recommended_brewing": "Рекомендуемое заваривание",
    "beans.brewing_label": "Заваривание",
    "beans.hopper": "Бункер",
    "beans.hopper_left": "Левый бункер",
    "beans.hopper_right": "Правый бункер",
    "beans.autofill": "Заполнить через LLM",
    "beans.autofill_running": "Спрашиваю ассистента…",
    "beans.no_beans": "Сорта не добавлены.",
    "beans.no_producers": "Сначала добавьте производителя.",
    "beans.hopper.assigned": "✓ Бункер {hopper}: {bean}",
    "beans.hopper.mismatch": "Сохранение не подтверждено: WS вернул OK, но после обновления в бункере {hopper} лежит «{actual}» вместо «{expected}». Проверь логи HA.",
    "beans.hopper.failed": "Назначение в бункер {hopper} провалилось: {error}",

    "additives.title": "Добавки",
    "additives.syrups": "Сиропы",
    "additives.toppings": "Топинги",
    "additives.milk": "Типы молока",
    "additives.add": "Добавить",
    "additives.name": "Название",
    "additives.brand": "Бренд",
    "additives.notes": "Примечания",
    "additives.empty_syrups": "Сиропы не добавлены.",
    "additives.empty_toppings": "Топинги не добавлены.",
    "additives.empty_milk": "Типы молока не добавлены.",

    "sommelier.title": "AI Сомелье",
    "sommelier.prompt": "Опишите, что хотите выпить",
    "sommelier.generate": "Сгенерировать",
    "sommelier.brew_this": "Сварить",
    "sommelier.context_beans": "Зёрна в бункере",
    "sommelier.context_milk": "Доступное молоко",
    "sommelier.context_extras": "Доступные добавки",
    "sommelier.last_recipe": "Последний рецепт",
    "sommelier.no_recipe": "Попросите Сомелье собрать напиток.",
    "sommelier.brewing": "Отправляю команду варки…",
    "sommelier.brew_ok": "Варка началась.",
    "sommelier.brew_failed": "Варка не выполнена.",
    "sommelier.constraints_heading": "Ограничения и настроение",
    "sommelier.cup_size": "Объём чашки",
    "sommelier.mood_label": "Настроение (можно несколько)",
    "sommelier.occasion_label": "Повод (предложен по времени суток)",
    "sommelier.temperature_label": "Температура",
    "sommelier.caffeine_label": "Кофеин",
    "sommelier.dietary_label": "Диетические ограничения (можно несколько)",
    "sommelier.addins_heading": "Доступные добавки (мульти-выбор)",
    "sommelier.section_syrups": "Сиропы",
    "sommelier.section_toppings": "Топинги",
    "sommelier.section_milk": "Молоко",
    "sommelier.addin_unconfigured": "— не настроено в Добавках",
    "sommelier.machine_label": "Машина:",
    "sommelier.why": "Почему?",
    "sommelier.favorited_toast": "★ В избранном",
    "sommelier.fav_in": "В избранном",
    "sommelier.fav_add": "Добавить в избранное",
    "sommelier.favorite_failed": "Не удалось добавить в избранное",
    "sommelier.addins_load_failed": "Не удалось загрузить добавки",

    "sommelier.cup.espresso_cup": "Эспрессо-чашка",
    "sommelier.cup.cup": "Чашка",
    "sommelier.cup.mug": "Кружка",
    "sommelier.cup.tall_glass": "Высокий стакан",
    "sommelier.cup.travel": "Термокружка",

    "sommelier.mood.energizing": "Бодрящее",
    "sommelier.mood.relaxing": "Расслабляющее",
    "sommelier.mood.dessert": "Десертное",
    "sommelier.mood.classic": "Классическое",

    "sommelier.occasion.morning": "Утро",
    "sommelier.occasion.after_lunch": "После обеда",
    "sommelier.occasion.guests": "Гости",
    "sommelier.occasion.romantic": "Романтическое",
    "sommelier.occasion.work": "Работа",

    "sommelier.temp.auto": "Авто",
    "sommelier.temp.hot": "Горячий",
    "sommelier.temp.iced": "Холодный",

    "sommelier.caffeine.regular": "Обычный",
    "sommelier.caffeine.low": "Низкий",
    "sommelier.caffeine.decaf_evening": "Без кофеина (вечер)",

    "sommelier.diet.no_sugar": "Без сахара",
    "sommelier.diet.lactose_free": "Без лактозы",
    "sommelier.diet.low_calorie": "Низкокалорийное",
    "sommelier.diet.vegan": "Веганское",

    "modal.add_bean": "Добавить сорт",
    "modal.edit_bean": "Редактировать сорт",
    "modal.add_producer": "Добавить производителя",
    "modal.edit_producer": "Редактировать производителя",
    "modal.add_additive": "Добавить добавку",
    "modal.edit_additive": "Редактировать добавку",
    "modal.type": "Тип",
    "modal.type.syrup": "Сироп",
    "modal.type.topping": "Топинг",
    "modal.type.milk": "Молоко",

    "tags.title": "Вкусовые заметки",
    "tags.add_placeholder": "Добавить тег…",
    "tags.add_button": "Добавить тег",

    "hopper.title": "Назначение в бункер",
    "hopper.left": "Бункер 1 (левый)",
    "hopper.right": "Бункер 2 (правый)",
    "hopper.unassigned": "— не назначено —",
    "hopper.assigned": "Назначено",

    "settings.title": "Настройки",
    "settings.llm_agent": "LLM-модель",
    "settings.llm_help": "Conversation agent для Сомелье и автозаполнения зёрен.",
    "settings.prompts": "Шаблоны промптов",
    "settings.prompt_default": "По умолчанию",
    "settings.prompt_reset": "Сбросить",
    "settings.prompt_save": "Сохранить",
    "settings.saved": "Сохранено.",
    "settings.reset_done": "Сброшено к дефолту.",
    "settings.help_title": "Как работают шаблоны промптов",
    "settings.help_placeholders": "Подстановки",
    "settings.help_no_placeholders": "У этого слота нет подстановок.",
    "settings.help_syntax": "Синтаксис",
    "settings.help_syntax_text":
      "Используется Python str.format: {name} — значение, {name!r} — значение в кавычках. " +
      "Неизвестные подстановки попадут в промпт буквально, чтобы их можно было заметить в ответе LLM.",
    "settings.help_schema":
      "Если ниже показан JSON Schema — он автоматически дописывается к вашему шаблону перед " +
      "отправкой запроса. Модели предписано отвечать строгим JSON по этой схеме; ответы " +
      "валидируются на сервере, при ошибке выполняется один авто-retry с описанием ошибок.",
    "settings.help_smartchain":
      "Если установлена интеграция SmartChain и выбран её агент — запрос пойдёт через её " +
      "нативный structured-output API (без парсинга — провайдер возвращает строгий объект). " +
      "Иначе работает текстовый путь с валидацией.",
    "settings.preview": "Посмотреть собранный промпт",
    "settings.preview_title": "Собранный промпт",
    "settings.preview_loading": "Собираю превью…",

    "diag.llm_calls": "Последние LLM-запросы",
    "diag.no_llm_calls": "LLM-запросов ещё не было.",
    "diag.llm_show_prompt": "Показать промпт",
    "diag.llm_show_response": "Показать ответ",
  },
};

/**
 * Resolve a translation key.
 *
 * @param {string} key       Dot-notated key like "status.firmware".
 * @param {string} [lang]    HA language code (e.g. "ru", "en"). Defaults to "en".
 * @param {Object} [params]  Optional substitution map; "{name}" tokens are
 *                           replaced with `params.name`.
 * @returns {string} The translated string, or the key itself if missing.
 */
export function t(key, lang = "en", params = null) {
  const dict = STRINGS[lang] || STRINGS.en;
  let value = dict[key];
  if (value === undefined) {
    value = STRINGS.en[key];
  }
  if (value === undefined) {
    return key;
  }
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      value = value.replaceAll(`{${k}}`, String(v));
    }
  }
  return value;
}

/** Convenience helper: returns a t() bound to a single language. */
export function makeT(lang) {
  return (key, params) => t(key, lang, params);
}

export const SUPPORTED_LANGUAGES = Object.keys(STRINGS);
