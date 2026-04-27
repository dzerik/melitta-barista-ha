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

    "common.loading": "Loading…",
    "common.error": "Error",
    "common.save": "Save",
    "common.cancel": "Cancel",
    "common.delete": "Delete",
    "common.add": "Add",
    "common.edit": "Edit",
    "common.refresh": "Refresh",
    "common.yes": "Yes",
    "common.no": "No",
    "common.unknown": "Unknown",
    "common.never": "Never",
    "common.empty": "Empty",

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
    "beans.hopper": "Hopper",
    "beans.hopper_left": "Left hopper",
    "beans.hopper_right": "Right hopper",
    "beans.autofill": "Fill from LLM",
    "beans.autofill_running": "Asking the assistant…",
    "beans.no_beans": "No beans added yet.",
    "beans.no_producers": "No producers yet — add one first.",

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

    "common.loading": "Загрузка…",
    "common.error": "Ошибка",
    "common.save": "Сохранить",
    "common.cancel": "Отмена",
    "common.delete": "Удалить",
    "common.add": "Добавить",
    "common.edit": "Изменить",
    "common.refresh": "Обновить",
    "common.yes": "Да",
    "common.no": "Нет",
    "common.unknown": "—",
    "common.never": "Никогда",
    "common.empty": "Пусто",

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
    "beans.hopper": "Бункер",
    "beans.hopper_left": "Левый бункер",
    "beans.hopper_right": "Правый бункер",
    "beans.autofill": "Заполнить через LLM",
    "beans.autofill_running": "Спрашиваю ассистента…",
    "beans.no_beans": "Сорта не добавлены.",
    "beans.no_producers": "Сначала добавьте производителя.",

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
