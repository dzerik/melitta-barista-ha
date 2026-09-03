#!/usr/bin/env python3
"""One-off seeding script for ``custom_components/melitta_barista/ui_strings/``.

Generates the 29 ``ui_strings/<locale>.json`` flat maps of UI Contract v2
(§6.3.3/§6.3.4) by porting this project's own existing 29-locale material:

* the dormant ``entity.*`` blocks in ``translations/<locale>.json``
  (status/sub-process/manipulation states, the 24 recipe names, the 12
  button names);
* the panel bundles ``www/i18n/locales/<locale>.js`` (DirectKey category
  labels, hopper nouns for the blend labels, "Warm milk");
* the card bundles ``src/localize/languages/<locale>.json`` in the
  sibling ``melitta-barista-card`` repo (freestyle value tokens, the two
  extra manipulation strings, maintenance descriptions and group labels).

Material that exists nowhere yet (info messages, milk-drink category,
hot milk, the chilled Nivona drinks, factory-reset labels, four group
labels) is newly authored below in all 29 languages, matching the
terminology of the ported strings. The blend labels are derived from the
panel hopper strings by stripping the "(left)/(right)" parenthetical.

v3 (0.93, UI Contract §9.1.4/§9.2.5) adds the ``settings.*`` and
``sommelier.*`` domains:

* ``settings.<setting>.label`` — Melitta tokens ported ×29 from the
  translated ``entity.{switch,number}`` blocks; Nivona-only tokens are
  seeded into en.json ONLY (their anchored entity-name strings are
  test-frozen to English in all 29 ``translations/`` files per §9.1.2.1,
  so a mechanical ×29 port would just duplicate English — sparse locales
  overlay en per §6.3.3);
* ``settings.<setting>.description`` — 6 tokens ported en/de/ru from the
  PWA ``src/locales`` (sparse-others, §6.3.3);
* ``settings.{water_hardness,brew_temperature}.levels.*`` — ported ×29
  from the card ``settings.levels.*`` bundles, re-keyed numeric→token;
* ``settings._levels.{off,on}``, ``settings._groups.*`` — newly authored
  ×29 (the shared tier / group headers);
* ``sommelier.{mood,occasion,cup_size,temperature,caffeine,dietary}.*``
  — 24 tokens ported ×29 from the panel bundles (re-keyed cup→cup_size,
  temp→temperature, diet→dietary);
* ``sommelier.{roast,bean_type,origin,mode}.*`` — 11 tokens ported
  en/de/ru from the PWA, newly authored ×26;
* ``sommelier.extras_kind.*`` — singular kind labels newly authored ×29
  (the PWA/panel sources carry only plural section headers), matching
  each language's panel syrups/toppings terminology;
* ``actions.save_directkey.label`` — newly authored ×29 ("Direct Key"
  kept Latin per the house feature-name convention).

This script is a development tool — it is NOT shipped in the component
and is safe to re-run (output is deterministic). Run from the repo root:

    python3 scripts/seed_ui_strings.py [--card-repo ../melitta-barista-card] \\
        [--app-repo ../melitta-barista-app]

It never writes to ``strings.json`` or ``translations/`` — those stay
untouched (hassfest-validated surfaces).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPONENT = REPO_ROOT / "custom_components" / "melitta_barista"
OUT_DIR = COMPONENT / "ui_strings"

# §6.3.4 token → dormant-translation-key mappings.
PROCESS_TOKEN_TO_STATE = {
    "READY": "ready", "PRODUCT": "brewing", "CLEANING": "cleaning",
    "DESCALING": "descaling", "FILTER_INSERT": "filter_insert",
    "FILTER_REPLACE": "filter_replace", "FILTER_REMOVE": "filter_remove",
    "SWITCH_OFF": "off", "EASY_CLEAN": "easy_clean",
    "INTENSIVE_CLEAN": "intensive_clean", "EVAPORATING": "evaporating",
    "BUSY": "busy",
}
SUB_PROCESS_TOKEN_TO_STATE = {
    "GRINDING": "grinding", "COFFEE": "extracting", "STEAM": "steaming",
    "WATER": "dispensing_water", "PREPARE": "preparing",
}
MANIPULATION_TOKEN_TO_STATE = {
    "NONE": "none", "BU_REMOVED": "brew_unit_removed",
    "TRAYS_MISSING": "trays_missing", "EMPTY_TRAYS": "empty_trays",
    "FILL_WATER": "fill_water", "CLOSE_POWDER_LID": "close_powder_lid",
    "FILL_POWDER": "fill_powder",
}
# The two manipulation tokens with 29-language material only in the card.
MANIPULATION_TOKEN_TO_CARD = {
    "MOVE_CUP_TO_FROTHER": "move_cup_to_frother",
    "FLUSH_REQUIRED": "flush_required",
}

# 24 Melitta recipe name_keys == the dormant select.recipe.state keys.
MELITTA_NAME_KEYS = (
    "espresso", "ristretto", "lungo", "espresso_doppio", "ristretto_doppio",
    "cafe_creme", "cafe_creme_doppio", "americano", "americano_extra",
    "long_black", "red_eye", "black_eye", "dead_eye", "cappuccino",
    "espresso_macchiato", "caffe_latte", "cafe_au_lait", "flat_white",
    "latte_macchiato", "latte_macchiato_extra", "latte_macchiato_triple",
    "milk", "milk_froth", "hot_water",
)

# Freestyle value families → card `values.*` keys (card-only `extra_strong`
# is deliberately never served, §6.3.4).
VALUE_FAMILIES = {
    "process": ("none", "coffee", "milk", "water"),
    "intensity": ("very_mild", "mild", "medium", "strong", "very_strong"),
    "aroma": ("standard", "intense"),
    "temperature": ("cold", "normal", "high"),
    "shots": ("none", "one", "two", "three"),
}

DIRECTKEY_CATEGORIES = (
    "espresso", "cafe_creme", "cappuccino", "latte_macchiato",
    "milk_froth", "milk", "water",
)

# 12 existing button actions with dormant `entity.button.<key>.name`.
BUTTON_ACTIONS = (
    "brew", "cancel", "easy_clean", "intensive_clean", "descaling",
    "switch_off", "filter_insert", "filter_replace", "filter_remove",
    "evaporating", "reset_recipe", "confirm_prompt",
)
# 8 actions with card-bundle descriptions (§6.3.4 — the rest have none).
DESCRIBED_ACTIONS = (
    "easy_clean", "intensive_clean", "descaling", "evaporating",
    "filter_insert", "filter_replace", "filter_remove", "switch_off",
)

# Feature names kept untranslated everywhere (house style: the card ships
# "Freestyle" verbatim in all 29 bundles; "Direct Key" and drink names
# like "Cream" follow the same Latin-name convention).
CONSTANTS = {
    "actions.brew_freestyle.label": "Freestyle",
    "actions.brew_directkey.label": "Direct Key",
    "recipes.name.cream": "Cream",
    "recipes.category.my_coffee": "My Coffee",
}

# English overrides where the ported source's casing/wording differs from
# the descriptor's English display name.
EN_OVERRIDES = {
    "recipes.name.frothy_milk": "Frothy Milk",
    "recipes.name.warm_milk": "Warm Milk",
}

_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*$")
_JS_PAIR = re.compile(
    r'^\s*"((?:[^"\\]|\\.)*)":\s*"((?:[^"\\]|\\.)*)",?\s*$', re.M
)


def derive_name_key(display_name: str) -> str:
    """§6.3.6 seeding-default derivation: NFKD → strip diacritics →
    lowercase → spaces/hyphens → underscores. Hand-reviewed, never used
    at runtime."""
    normalized = unicodedata.normalize("NFKD", display_name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[\s-]+", "_", ascii_only.strip().lower())


# ---------------------------------------------------------------------------
# Newly authored strings (16 per locale), keyed by final flat key.
# Terminology matches the ported material (hopper nouns from the panel,
# drink names kept Latin per the dormant recipe blocks).
# ---------------------------------------------------------------------------
_AUTHORED_KEYS = (
    "status.info_message.FILL_BEANS_1",
    "status.info_message.FILL_BEANS_2",
    "status.info_message.EASY_CLEAN",
    "status.info_message.POWDER_FILLED",
    "status.info_message.PREPARATION_CANCELLED",
    "recipes.category.milk_drink",
    "recipes.name.hot_milk",
    "recipes.name.chilled_espresso",
    "recipes.name.chilled_lungo",
    "recipes.name.chilled_americano",
    "actions.factory_reset_settings.label",
    "actions.factory_reset_recipes.label",
    "actions._groups.brew",
    "actions._groups.control",
    "actions._groups.power",
    "actions._groups.danger",
)

AUTHORED: dict[str, tuple[str, ...]] = {
    "en": ("Fill bean hopper 1", "Fill bean hopper 2",
           "Easy Clean recommended", "Ground coffee filled",
           "Preparation cancelled", "Milk drink", "Hot Milk",
           "Chilled Espresso", "Chilled Lungo", "Chilled Americano",
           "Factory reset settings", "Factory reset recipes",
           "Brew", "Control", "Power", "Danger zone"),
    "bg": ("Напълнете бункер 1", "Напълнете бункер 2",
           "Препоръчва се Easy Clean", "Поставено е мляно кафе",
           "Приготвянето е отменено", "Млечна напитка", "Горещо мляко",
           "Студено Espresso", "Студено Lungo", "Студено Americano",
           "Фабрично нулиране на настройките",
           "Фабрично нулиране на рецептите",
           "Приготвяне", "Управление", "Захранване", "Опасна зона"),
    "bs": ("Napunite spremnik 1", "Napunite spremnik 2",
           "Preporučuje se Easy Clean", "Mljevena kafa je dodana",
           "Priprema je otkazana", "Mliječni napitak", "Vruće mlijeko",
           "Hladni Espresso", "Hladni Lungo", "Hladni Americano",
           "Vraćanje postavki na tvorničke",
           "Vraćanje recepata na tvorničke",
           "Priprema", "Upravljanje", "Napajanje", "Opasna zona"),
    "cs": ("Naplňte zásobník 1", "Naplňte zásobník 2",
           "Doporučuje se Easy Clean", "Mletá káva vložena",
           "Příprava zrušena", "Mléčný nápoj", "Horké mléko",
           "Chlazené Espresso", "Chlazené Lungo", "Chlazené Americano",
           "Tovární reset nastavení", "Tovární reset receptů",
           "Příprava", "Ovládání", "Napájení", "Nebezpečná zóna"),
    "da": ("Fyld beholder 1", "Fyld beholder 2",
           "Easy Clean anbefales", "Malet kaffe påfyldt",
           "Tilberedning annulleret", "Mælkedrik", "Meget varm mælk",
           "Kold Espresso", "Kold Lungo", "Kold Americano",
           "Fabriksnulstilling af indstillinger",
           "Fabriksnulstilling af opskrifter",
           "Brygning", "Betjening", "Strøm", "Farezone"),
    "de": ("Bohnenbehälter 1 füllen", "Bohnenbehälter 2 füllen",
           "Easy Clean empfohlen", "Kaffeepulver eingefüllt",
           "Zubereitung abgebrochen", "Milchgetränk", "Heiße Milch",
           "Gekühlter Espresso", "Gekühlter Lungo", "Gekühlter Americano",
           "Werksreset der Einstellungen", "Werksreset der Rezepte",
           "Brühen", "Steuerung", "Ein/Aus", "Gefahrenzone"),
    "el": ("Γεμίστε το δοχείο 1", "Γεμίστε το δοχείο 2",
           "Συνιστάται Easy Clean", "Προστέθηκε αλεσμένος καφές",
           "Η παρασκευή ακυρώθηκε", "Ρόφημα γάλακτος", "Καυτό γάλα",
           "Κρύο Espresso", "Κρύο Lungo", "Κρύο Americano",
           "Εργοστασιακή επαναφορά ρυθμίσεων",
           "Εργοστασιακή επαναφορά συνταγών",
           "Παρασκευή", "Έλεγχος", "Τροφοδοσία", "Επικίνδυνη ζώνη"),
    "es": ("Llene la tolva 1", "Llene la tolva 2",
           "Se recomienda Easy Clean", "Café molido añadido",
           "Preparación cancelada", "Bebida con leche",
           "Leche muy caliente",
           "Espresso frío", "Lungo frío", "Americano frío",
           "Restablecer ajustes de fábrica",
           "Restablecer recetas de fábrica",
           "Preparación", "Control", "Alimentación", "Zona de peligro"),
    "et": ("Täitke mahuti 1", "Täitke mahuti 2",
           "Soovitatav on Easy Clean", "Jahvatatud kohv lisatud",
           "Valmistamine tühistatud", "Piimajook", "Kuum piim",
           "Jahutatud Espresso", "Jahutatud Lungo", "Jahutatud Americano",
           "Seadete tehaselähtestus", "Retseptide tehaselähtestus",
           "Valmistamine", "Juhtimine", "Toide", "Ohtlik tsoon"),
    "fi": ("Täytä säiliö 1", "Täytä säiliö 2",
           "Easy Clean suositellaan", "Kahvijauhe lisätty",
           "Valmistus peruutettu", "Maitojuoma", "Kuuma maito",
           "Kylmä Espresso", "Kylmä Lungo", "Kylmä Americano",
           "Asetusten tehdaspalautus", "Reseptien tehdaspalautus",
           "Valmistus", "Ohjaus", "Virta", "Vaaravyöhyke"),
    "fr": ("Remplir le bac 1", "Remplir le bac 2",
           "Easy Clean recommandé", "Café moulu ajouté",
           "Préparation annulée", "Boisson lactée", "Lait très chaud",
           "Espresso froid", "Lungo froid", "Americano froid",
           "Réinitialisation d'usine des réglages",
           "Réinitialisation d'usine des recettes",
           "Préparation", "Contrôle", "Alimentation", "Zone de danger"),
    "hr": ("Napunite spremnik 1", "Napunite spremnik 2",
           "Preporučuje se Easy Clean", "Mljevena kava je dodana",
           "Priprema je otkazana", "Mliječni napitak", "Vruće mlijeko",
           "Hladni Espresso", "Hladni Lungo", "Hladni Americano",
           "Tvorničko resetiranje postavki",
           "Tvorničko resetiranje recepata",
           "Priprema", "Upravljanje", "Napajanje", "Opasna zona"),
    "hu": ("Töltse fel az 1. tartályt", "Töltse fel a 2. tartályt",
           "Easy Clean ajánlott", "Őrölt kávé betöltve",
           "A készítés megszakítva", "Tejes ital", "Forró tej",
           "Hideg Espresso", "Hideg Lungo", "Hideg Americano",
           "Beállítások gyári visszaállítása",
           "Receptek gyári visszaállítása",
           "Főzés", "Vezérlés", "Tápellátás", "Veszélyzóna"),
    "it": ("Riempire il contenitore 1", "Riempire il contenitore 2",
           "Easy Clean consigliato", "Caffè macinato inserito",
           "Preparazione annullata", "Bevanda al latte",
           "Latte molto caldo",
           "Espresso freddo", "Lungo freddo", "Americano freddo",
           "Ripristino di fabbrica delle impostazioni",
           "Ripristino di fabbrica delle ricette",
           "Preparazione", "Controllo", "Alimentazione",
           "Zona di pericolo"),
    "lt": ("Pripildykite talpyklą 1", "Pripildykite talpyklą 2",
           "Rekomenduojama Easy Clean", "Įpilta maltos kavos",
           "Ruošimas atšauktas", "Pieno gėrimas", "Karštas pienas",
           "Šaltas Espresso", "Šaltas Lungo", "Šaltas Americano",
           "Gamyklinis nustatymų atstatymas",
           "Gamyklinis receptų atstatymas",
           "Ruošimas", "Valdymas", "Maitinimas", "Pavojinga zona"),
    "lv": ("Piepildiet tvertni 1", "Piepildiet tvertni 2",
           "Ieteicams Easy Clean", "Ievietota malta kafija",
           "Gatavošana atcelta", "Piena dzēriens", "Karsts piens",
           "Auksts Espresso", "Auksts Lungo", "Auksts Americano",
           "Iestatījumu rūpnīcas atiestate",
           "Recepšu rūpnīcas atiestate",
           "Gatavošana", "Vadība", "Barošana", "Bīstamā zona"),
    "mk": ("Наполнете го резервоарот 1", "Наполнете го резервоарот 2",
           "Се препорачува Easy Clean", "Додадено мелено кафе",
           "Подготовката е откажана", "Млечен пијалок", "Жешко млеко",
           "Ладно Espresso", "Ладно Lungo", "Ладно Americano",
           "Фабричко ресетирање на поставките",
           "Фабричко ресетирање на рецептите",
           "Подготовка", "Управување", "Напојување", "Опасна зона"),
    "nb": ("Fyll beholder 1", "Fyll beholder 2",
           "Easy Clean anbefales", "Malt kaffe fylt på",
           "Tilberedning avbrutt", "Melkedrikk", "Ekstra varm melk",
           "Kald Espresso", "Kald Lungo", "Kald Americano",
           "Fabrikktilbakestilling av innstillinger",
           "Fabrikktilbakestilling av oppskrifter",
           "Brygging", "Styring", "Strøm", "Faresone"),
    "nl": ("Vul reservoir 1", "Vul reservoir 2",
           "Easy Clean aanbevolen", "Gemalen koffie toegevoegd",
           "Bereiding geannuleerd", "Melkdrank", "Hete melk",
           "Koude Espresso", "Koude Lungo", "Koude Americano",
           "Fabrieksreset van instellingen", "Fabrieksreset van recepten",
           "Bereiding", "Bediening", "Aan/uit", "Gevarenzone"),
    "pl": ("Napełnij pojemnik 1", "Napełnij pojemnik 2",
           "Zalecane Easy Clean", "Wsypano kawę mieloną",
           "Przygotowanie anulowane", "Napój mleczny", "Gorące mleko",
           "Schłodzone Espresso", "Schłodzone Lungo",
           "Schłodzone Americano",
           "Przywracanie ustawień fabrycznych",
           "Przywracanie fabrycznych przepisów",
           "Parzenie", "Sterowanie", "Zasilanie", "Strefa niebezpieczna"),
    "pt": ("Encha o depósito 1", "Encha o depósito 2",
           "Easy Clean recomendado", "Café moído adicionado",
           "Preparação cancelada", "Bebida com leite", "Leite quente",
           "Espresso frio", "Lungo frio", "Americano frio",
           "Reposição de fábrica das definições",
           "Reposição de fábrica das receitas",
           "Preparação", "Controlo", "Energia", "Zona de perigo"),
    "ro": ("Umpleți rezervorul 1", "Umpleți rezervorul 2",
           "Easy Clean recomandat", "Cafea măcinată adăugată",
           "Preparare anulată", "Băutură cu lapte", "Lapte fierbinte",
           "Espresso rece", "Lungo rece", "Americano rece",
           "Resetare din fabrică a setărilor",
           "Resetare din fabrică a rețetelor",
           "Preparare", "Control", "Alimentare", "Zonă de pericol"),
    "ru": ("Наполните бункер 1", "Наполните бункер 2",
           "Рекомендуется Easy Clean", "Молотый кофе засыпан",
           "Приготовление отменено", "Молочный напиток",
           "Горячее молоко",
           "Холодный Espresso", "Холодный Lungo", "Холодный Americano",
           "Сброс настроек к заводским", "Сброс рецептов к заводским",
           "Приготовление", "Управление", "Питание", "Опасная зона"),
    "sk": ("Naplňte zásobník 1", "Naplňte zásobník 2",
           "Odporúča sa Easy Clean", "Mletá káva vložená",
           "Príprava zrušená", "Mliečny nápoj", "Horúce mlieko",
           "Chladené Espresso", "Chladené Lungo", "Chladené Americano",
           "Továrenský reset nastavení", "Továrenský reset receptov",
           "Príprava", "Ovládanie", "Napájanie", "Nebezpečná zóna"),
    "sl": ("Napolnite posodo 1", "Napolnite posodo 2",
           "Priporočen Easy Clean", "Mleta kava dodana",
           "Priprava preklicana", "Mlečni napitek", "Vroče mleko",
           "Hladni Espresso", "Hladni Lungo", "Hladni Americano",
           "Tovarniška ponastavitev nastavitev",
           "Tovarniška ponastavitev receptov",
           "Priprava", "Upravljanje", "Napajanje", "Nevarno območje"),
    "sr": ("Напуните резервоар 1", "Напуните резервоар 2",
           "Препоручује се Easy Clean", "Млевена кафа је додата",
           "Припрема је отказана", "Млечни напитак", "Вруће млеко",
           "Хладни Espresso", "Хладни Lungo", "Хладни Americano",
           "Фабричко ресетовање подешавања",
           "Фабричко ресетовање рецепата",
           "Припрема", "Управљање", "Напајање", "Опасна зона"),
    "sv": ("Fyll behållare 1", "Fyll behållare 2",
           "Easy Clean rekommenderas", "Malet kaffe påfyllt",
           "Tillredning avbruten", "Mjölkdryck", "Het mjölk",
           "Kall Espresso", "Kall Lungo", "Kall Americano",
           "Fabriksåterställning av inställningar",
           "Fabriksåterställning av recept",
           "Bryggning", "Styrning", "Ström", "Riskzon"),
    "tr": ("Hazne 1'i doldurun", "Hazne 2'yi doldurun",
           "Easy Clean önerilir", "Çekilmiş kahve eklendi",
           "Hazırlama iptal edildi", "Sütlü içecek", "Sıcak süt",
           "Soğuk Espresso", "Soğuk Lungo", "Soğuk Americano",
           "Ayarları fabrika ayarlarına sıfırlama",
           "Tarifleri fabrika ayarlarına sıfırlama",
           "Hazırlama", "Kontrol", "Güç", "Tehlikeli bölge"),
    "uk": ("Наповніть бункер 1", "Наповніть бункер 2",
           "Рекомендується Easy Clean", "Мелену каву засипано",
           "Приготування скасовано", "Молочний напій",
           "Гаряче молоко",
           "Холодний Espresso", "Холодний Lungo", "Холодний Americano",
           "Скидання налаштувань до заводських",
           "Скидання рецептів до заводських",
           "Приготування", "Керування", "Живлення", "Небезпечна зона"),
}


# ---------------------------------------------------------------------------
# v3 (0.93): settings + sommelier domains (§9.1.4/§9.2.5)
# ---------------------------------------------------------------------------

# Melitta setting tokens → dormant translated entity-name blocks (×29).
MELITTA_SETTING_LABELS = {
    "energy_saving": ("switch", "energy_saving"),
    "auto_bean_select": ("switch", "auto_bean_select"),
    "rinsing_disabled": ("switch", "rinsing_disabled"),
    "water_hardness": ("number", "water_hardness"),
    "auto_off_after": ("number", "auto_off_after"),
    "brew_temperature": ("number", "brew_temperature"),
    "language": ("number", "language"),
    "filter": ("number", "filter"),
}

# Nivona-only setting tokens → anchored entity-name blocks. These name
# strings are frozen slug-equal (English) in ALL 29 translations files
# (§9.1.2.1), so they are seeded into en.json only — sparse locales
# overlay English per key (§6.3.3).
NIVONA_SETTING_LABELS = {
    "auto_off": ("select", "auto_off"),
    "auto_on_deactivated": ("select", "auto_on_deactivated"),
    "auto_on_hours": ("number", "auto_on_hours"),
    "auto_on_minutes": ("number", "auto_on_minutes"),
    "coffee_temperature": ("select", "coffee_temperature"),
    "cup_heater": ("select", "cup_heater"),
    "direct_start_deactivated": ("select", "direct_start_deactivated"),
    "milk_foam_temperature": ("select", "milk_foam_temperature"),
    "milk_products_active": ("select", "milk_products_active"),
    "milk_temperature": ("select", "milk_temperature"),
    "off_rinse": ("select", "off_rinse"),
    "power_on_frother_time": ("select", "power_on_frother_time"),
    "power_on_rinse": ("select", "power_on_rinse"),
    "profile": ("select", "profile"),
    "save_energy": ("select", "save_energy"),
    "tank_light": ("select", "tank_light"),
    "tank_light_brightness": ("select", "tank_light_brightness"),
    "tank_light_color": ("select", "tank_light_color"),
    "temperature": ("select", "temperature"),
    "touch_lock": ("select", "touch_lock"),
    "water_temperature": ("select", "water_temperature"),
}

# Card `settings.levels.<setting>.<numeric>` → token re-keying (×29).
CARD_LEVEL_KEYS = {
    "settings.water_hardness.levels.soft": ("water_hardness", "1"),
    "settings.water_hardness.levels.medium": ("water_hardness", "2"),
    "settings.water_hardness.levels.hard": ("water_hardness", "3"),
    "settings.water_hardness.levels.very_hard": ("water_hardness", "4"),
    "settings.brew_temperature.levels.low": ("brew_temperature", "0"),
    "settings.brew_temperature.levels.normal": ("brew_temperature", "1"),
    "settings.brew_temperature.levels.high": ("brew_temperature", "2"),
}

# Panel sommelier families ×29 (vocab family → (panel prefix, tokens)).
PANEL_SOMMELIER = {
    "mood": ("mood", ("energizing", "relaxing", "dessert", "classic")),
    "occasion": ("occasion",
                 ("morning", "after_lunch", "guests", "romantic", "work")),
    "cup_size": ("cup", ("espresso_cup", "cup", "mug", "tall_glass", "travel")),
    "temperature": ("temp", ("auto", "hot", "iced")),
    "caffeine": ("caffeine", ("regular", "low", "decaf_evening")),
    "dietary": ("diet", ("no_sugar", "lactose_free", "low_calorie", "vegan")),
}

# PWA-sourced material — the PWA ships en/de/ru only (§9.2.5/§9.1.4).
PWA_LOCALES = ("en", "de", "ru")
PWA_SOMMELIER = {
    "sommelier.roast.light": "sommelier.roast_light",
    "sommelier.roast.medium": "sommelier.roast_medium",
    "sommelier.roast.medium_dark": "sommelier.roast_medium_dark",
    "sommelier.roast.dark": "sommelier.roast_dark",
    "sommelier.bean_type.arabica": "sommelier.type_arabica",
    "sommelier.bean_type.arabica_robusta": "sommelier.type_arabica_robusta",
    "sommelier.bean_type.robusta": "sommelier.type_robusta",
    "sommelier.origin.single_origin": "sommelier.origin_single",
    "sommelier.origin.blend": "sommelier.origin_blend",
    "sommelier.mode.surprise_me": "sommelier.surprise_me",
    "sommelier.mode.custom": "sommelier.custom",
}
PWA_SETTING_DESCRIPTIONS = {
    "settings.energy_saving.description": "settings.energy_saving_desc",
    "settings.auto_bean_select.description": "settings.auto_bean_desc",
    "settings.rinsing_disabled.description": "settings.rinsing_desc",
    "settings.water_hardness.description": "settings.water_hardness_desc",
    "settings.auto_off_after.description": "settings.auto_off_desc",
    "settings.brew_temperature.description": "settings.brew_temp_desc",
}

# Newly authored ×29: shared level tier, settings group headers, the
# save_directkey action label ("Direct Key" kept Latin), and the singular
# extras-kind labels (sources ship only plural section headers; the
# singulars follow each language's panel syrups/toppings terminology).
_AUTHORED_V3_KEYS = (
    "settings._levels.off",
    "settings._levels.on",
    "settings._groups.brew",
    "settings._groups.water",
    "settings._groups.power",
    "settings._groups.system",
    "actions.save_directkey.label",
    "sommelier.extras_kind.syrup",
    "sommelier.extras_kind.topping",
    "sommelier.extras_kind.liqueur",
)

AUTHORED_V3: dict[str, tuple[str, ...]] = {
    "en": ("Off", "On", "Brewing", "Water", "Power", "System",
           "Save DirectKey Recipe", "Syrup", "Topping", "Liqueur"),
    "bg": ("Изключено", "Включено", "Приготвяне", "Вода", "Захранване",
           "Система", "Запази рецепта Direct Key",
           "Сироп", "Топинг", "Ликьор"),
    "bs": ("Isključeno", "Uključeno", "Priprema", "Voda", "Napajanje",
           "Sistem", "Sačuvaj Direct Key recept",
           "Sirup", "Preliv", "Liker"),
    "cs": ("Vypnuto", "Zapnuto", "Příprava", "Voda", "Napájení",
           "Systém", "Uložit recept Direct Key",
           "Sirup", "Topping", "Likér"),
    "da": ("Fra", "Til", "Brygning", "Vand", "Strøm",
           "System", "Gem Direct Key-opskrift",
           "Sirup", "Topping", "Likør"),
    "de": ("Aus", "Ein", "Brühen", "Wasser", "Energie",
           "System", "Direct-Key-Rezept speichern",
           "Sirup", "Topping", "Likör"),
    "el": ("Ανενεργό", "Ενεργό", "Παρασκευή", "Νερό", "Τροφοδοσία",
           "Σύστημα", "Αποθήκευση συνταγής Direct Key",
           "Σιρόπι", "Γαρνιτούρα", "Λικέρ"),
    "es": ("Apagado", "Encendido", "Preparación", "Agua", "Alimentación",
           "Sistema", "Guardar receta Direct Key",
           "Sirope", "Topping", "Licor"),
    "et": ("Väljas", "Sees", "Valmistamine", "Vesi", "Toide",
           "Süsteem", "Salvesta Direct Key retsept",
           "Siirup", "Kate", "Liköör"),
    "fi": ("Pois", "Päällä", "Valmistus", "Vesi", "Virta",
           "Järjestelmä", "Tallenna Direct Key -resepti",
           "Siirappi", "Täyte", "Likööri"),
    "fr": ("Désactivé", "Activé", "Préparation", "Eau", "Alimentation",
           "Système", "Enregistrer la recette Direct Key",
           "Sirop", "Garniture", "Liqueur"),
    "hr": ("Isključeno", "Uključeno", "Priprema", "Voda", "Napajanje",
           "Sustav", "Spremi Direct Key recept",
           "Sirup", "Preljev", "Liker"),
    "hu": ("Ki", "Be", "Főzés", "Víz", "Tápellátás",
           "Rendszer", "Direct Key recept mentése",
           "Szirup", "Feltét", "Likőr"),
    "it": ("Spento", "Acceso", "Preparazione", "Acqua", "Alimentazione",
           "Sistema", "Salva ricetta Direct Key",
           "Sciroppo", "Topping", "Liquore"),
    "lt": ("Išjungta", "Įjungta", "Ruošimas", "Vanduo", "Maitinimas",
           "Sistema", "Išsaugoti Direct Key receptą",
           "Sirupas", "Užpilas", "Likeris"),
    "lv": ("Izslēgts", "Ieslēgts", "Gatavošana", "Ūdens", "Barošana",
           "Sistēma", "Saglabāt Direct Key recepti",
           "Sīrups", "Papildinājums", "Liķieris"),
    "mk": ("Исклучено", "Вклучено", "Подготовка", "Вода", "Напојување",
           "Систем", "Зачувај Direct Key рецепт",
           "Сируп", "Прелив", "Ликер"),
    "nb": ("Av", "På", "Brygging", "Vann", "Strøm",
           "System", "Lagre Direct Key-oppskrift",
           "Sirup", "Topping", "Likør"),
    "nl": ("Uit", "Aan", "Bereiding", "Water", "Energie",
           "Systeem", "Direct Key-recept opslaan",
           "Siroop", "Topping", "Likeur"),
    "pl": ("Wyłączone", "Włączone", "Parzenie", "Woda", "Zasilanie",
           "System", "Zapisz przepis Direct Key",
           "Syrop", "Topping", "Likier"),
    "pt": ("Desligado", "Ligado", "Preparação", "Água", "Energia",
           "Sistema", "Guardar receita Direct Key",
           "Xarope", "Topping", "Licor"),
    "ro": ("Oprit", "Pornit", "Preparare", "Apă", "Alimentare",
           "Sistem", "Salvează rețeta Direct Key",
           "Sirop", "Topping", "Lichior"),
    "ru": ("Выкл", "Вкл", "Приготовление", "Вода", "Питание",
           "Система", "Сохранить рецепт Direct Key",
           "Сироп", "Топинг", "Ликёр"),
    "sk": ("Vypnuté", "Zapnuté", "Príprava", "Voda", "Napájanie",
           "Systém", "Uložiť recept Direct Key",
           "Sirup", "Posýpka", "Likér"),
    "sl": ("Izklopljeno", "Vklopljeno", "Priprava", "Voda", "Napajanje",
           "Sistem", "Shrani Direct Key recept",
           "Sirup", "Preliv", "Liker"),
    "sr": ("Искључено", "Укључено", "Припрема", "Вода", "Напајање",
           "Систем", "Сачувај Direct Key рецепт",
           "Сируп", "Прелив", "Ликер"),
    "sv": ("Av", "På", "Bryggning", "Vatten", "Ström",
           "System", "Spara Direct Key-recept",
           "Sirap", "Topping", "Likör"),
    "tr": ("Kapalı", "Açık", "Hazırlama", "Su", "Güç",
           "Sistem", "Direct Key tarifini kaydet",
           "Şurup", "Sos", "Likör"),
    "uk": ("Вимк", "Увімк", "Приготування", "Вода", "Живлення",
           "Система", "Зберегти рецепт Direct Key",
           "Сироп", "Топінг", "Лікер"),
}

# Newly authored ×26 (en/de/ru come from the PWA port instead): roast,
# bean_type, origin, and mode tokens. Bean varieties stay Latin in
# Latin-script locales and are transliterated in Cyrillic ones, matching
# the ru PWA precedent.
_AUTHORED_SOMMELIER_KEYS = (
    "sommelier.roast.light", "sommelier.roast.medium",
    "sommelier.roast.medium_dark", "sommelier.roast.dark",
    "sommelier.bean_type.arabica", "sommelier.bean_type.arabica_robusta",
    "sommelier.bean_type.robusta",
    "sommelier.origin.single_origin", "sommelier.origin.blend",
    "sommelier.mode.surprise_me", "sommelier.mode.custom",
)

AUTHORED_SOMMELIER: dict[str, tuple[str, ...]] = {
    "bg": ("Светло", "Средно", "Средно-тъмно", "Тъмно",
           "Арабика", "Арабика + Робуста", "Робуста",
           "Единичен произход", "Смес", "Изненадай ме", "Персонализиран"),
    "bs": ("Svijetlo", "Srednje", "Srednje tamno", "Tamno",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Jedno porijeklo", "Mješavina", "Iznenadi me", "Prilagođeno"),
    "cs": ("Světlé", "Střední", "Středně tmavé", "Tmavé",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Jednodruhová", "Směs", "Překvap mě", "Vlastní"),
    "da": ("Lys", "Mellem", "Mellemmørk", "Mørk",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Enkeltoprindelse", "Blanding", "Overrask mig",
           "Brugerdefineret"),
    "el": ("Ανοιχτό", "Μέτριο", "Μέτριο-σκούρο", "Σκούρο",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Μονοποικιλιακός", "Χαρμάνι", "Έκπληξέ με", "Προσαρμοσμένο"),
    "es": ("Claro", "Medio", "Medio-oscuro", "Oscuro",
           "Arábica", "Arábica + Robusta", "Robusta",
           "Origen único", "Mezcla", "Sorpréndeme", "Personalizado"),
    "et": ("Hele", "Keskmine", "Keskmiselt tume", "Tume",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Ühe päritoluga", "Segu", "Üllata mind", "Kohandatud"),
    "fi": ("Vaalea", "Keskipaahto", "Keskitumma", "Tumma",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Yhden alkuperän", "Sekoitus", "Yllätä minut", "Mukautettu"),
    "fr": ("Claire", "Moyenne", "Moyenne-foncée", "Foncée",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Origine unique", "Assemblage", "Surprends-moi",
           "Personnalisé"),
    "hr": ("Svijetlo", "Srednje", "Srednje tamno", "Tamno",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Jedno podrijetlo", "Mješavina", "Iznenadi me", "Prilagođeno"),
    "hu": ("Világos", "Közepes", "Közepesen sötét", "Sötét",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Egyeredetű", "Keverék", "Lepj meg", "Egyéni"),
    "it": ("Chiara", "Media", "Medio-scura", "Scura",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Monorigine", "Miscela", "Sorprendimi", "Personalizzato"),
    "lt": ("Šviesus", "Vidutinis", "Vidutiniškai tamsus", "Tamsus",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Vienos kilmės", "Mišinys", "Nustebink mane", "Pasirinktinis"),
    "lv": ("Gaišs", "Vidējs", "Vidēji tumšs", "Tumšs",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Vienas izcelsmes", "Maisījums", "Pārsteidz mani",
           "Pielāgots"),
    "mk": ("Светло", "Средно", "Средно-темно", "Темно",
           "Арабика", "Арабика + Робуста", "Робуста",
           "Едно потекло", "Мешавина", "Изненади ме", "Прилагодено"),
    "nb": ("Lys", "Middels", "Middels mørk", "Mørk",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Enkeltopprinnelse", "Blanding", "Overrask meg",
           "Egendefinert"),
    "nl": ("Licht", "Medium", "Middeldonker", "Donker",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Single origin", "Melange", "Verras me", "Aangepast"),
    "pl": ("Jasne", "Średnie", "Średnio ciemne", "Ciemne",
           "Arabika", "Arabika + Robusta", "Robusta",
           "Single origin", "Mieszanka", "Zaskocz mnie", "Własny"),
    "pt": ("Clara", "Média", "Média-escura", "Escura",
           "Arábica", "Arábica + Robusta", "Robusta",
           "Origem única", "Mistura", "Surpreende-me", "Personalizado"),
    "ro": ("Deschisă", "Medie", "Medie-închisă", "Închisă",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Origine unică", "Amestec", "Surprinde-mă", "Personalizat"),
    "sk": ("Svetlé", "Stredné", "Stredne tmavé", "Tmavé",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Jednodruhová", "Zmes", "Prekvap ma", "Vlastné"),
    "sl": ("Svetlo", "Srednje", "Srednje temno", "Temno",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Enega porekla", "Mešanica", "Preseneti me", "Po meri"),
    "sr": ("Светло", "Средње", "Средње тамно", "Тамно",
           "Арабика", "Арабика + Робуста", "Робуста",
           "Једно порекло", "Мешавина", "Изненади ме", "Прилагођено"),
    "sv": ("Ljus", "Mellan", "Mellanmörk", "Mörk",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Single origin", "Blandning", "Överraska mig", "Anpassad"),
    "tr": ("Açık", "Orta", "Orta-koyu", "Koyu",
           "Arabica", "Arabica + Robusta", "Robusta",
           "Tek kökenli", "Harman", "Beni şaşırt", "Özel"),
    "uk": ("Світле", "Середнє", "Середньо-темне", "Темне",
           "Арабіка", "Арабіка + Робуста", "Робуста",
           "Моносорт", "Бленд", "Здивуй мене", "Власний"),
}


def load_integration(locale: str) -> dict:
    """Load one translations/<locale>.json document."""
    path = COMPONENT / "translations" / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_panel(locale: str) -> dict[str, str]:
    """Extract the flat key/value pairs from a panel <locale>.js bundle."""
    text = (COMPONENT / "www" / "i18n" / "locales" / f"{locale}.js").read_text(
        encoding="utf-8"
    )
    pairs = {}
    for raw_key, raw_value in _JS_PAIR.findall(text):
        pairs[json.loads(f'"{raw_key}"')] = json.loads(f'"{raw_value}"')
    return pairs


def load_card(card_repo: Path, locale: str) -> dict:
    """Load one card src/localize/languages/<locale>.json bundle."""
    path = card_repo / "src" / "localize" / "languages" / f"{locale}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten(nested: dict, prefix: str = "") -> dict[str, str]:
    """Flatten a nested JSON object into dot-joined keys."""
    flat: dict[str, str] = {}
    for key, value in nested.items():
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{prefix}{key}."))
        else:
            flat[f"{prefix}{key}"] = value
    return flat


def load_app(app_repo: Path, locale: str) -> dict[str, str]:
    """Load one PWA src/locales/<locale>.json bundle, flattened."""
    path = app_repo / "src" / "locales" / f"{locale}.json"
    return _flatten(json.loads(path.read_text(encoding="utf-8")))


def strip_parenthetical(value: str) -> str:
    """Drop a trailing "(...)" — "Hopper 1 (left)" → "Hopper 1"."""
    return _PARENTHETICAL.sub("", value).strip()


def build_locale(
    locale: str, card_repo: Path, app_repo: Path,
) -> dict[str, str]:
    """Assemble the full flat map for one locale per the §6.3.4/§9.1.4/§9.2.5 tables."""
    integration = load_integration(locale)
    panel = load_panel(locale)
    card = load_card(card_repo, locale)
    entity = integration["entity"]
    out: dict[str, str] = {}

    # status.process / status.sub_process / status.manipulation — dormant
    # entity state blocks under token keys.
    state = entity["sensor"]["state"]["state"]
    for token, key in PROCESS_TOKEN_TO_STATE.items():
        out[f"status.process.{token}"] = state[key]
    activity = entity["sensor"]["activity"]["state"]
    for token, key in SUB_PROCESS_TOKEN_TO_STATE.items():
        out[f"status.sub_process.{token}"] = activity[key]
    action_required = entity["sensor"]["action_required"]["state"]
    for token, key in MANIPULATION_TOKEN_TO_STATE.items():
        out[f"status.manipulation.{token}"] = action_required[key]
    for token, key in MANIPULATION_TOKEN_TO_CARD.items():
        out[f"status.manipulation.{token}"] = card["action"][key]

    # values.<family>.<token> — card bundle (family-scoped keys; the
    # shared "none" feeds both process.none and shots.none).
    for family, tokens in VALUE_FAMILIES.items():
        for token in tokens:
            out[f"values.{family}.{token}"] = card["values"][token]
    # values.blend — derived from the panel hopper strings.
    out["values.blend.hopper_1"] = strip_parenthetical(panel["hopper.left"])
    out["values.blend.hopper_2"] = strip_parenthetical(panel["hopper.right"])
    # values.directkey_category — panel recipes.cat.* (same 7 tokens).
    for token in DIRECTKEY_CATEGORIES:
        out[f"values.directkey_category.{token}"] = panel[f"recipes.cat.{token}"]

    # recipes.name — 24 Melitta keys from the dormant select block.
    recipe_states = entity["select"]["recipe"]["state"]
    for name_key in MELITTA_NAME_KEYS:
        out[f"recipes.name.{name_key}"] = recipe_states[name_key]
    # Nivona-only names with existing homes: "Coffee" (card values),
    # "Warm Milk" (panel), "Frothy Milk" (dormant milk-froth block).
    out["recipes.name.coffee"] = card["values"]["coffee"]
    out["recipes.name.warm_milk"] = panel["recipes.cat.milk"]
    out["recipes.name.frothy_milk"] = recipe_states["milk_froth"]

    # recipes.category — espresso/water from the panel, coffee from the
    # card; milk_drink is authored, my_coffee is a constant.
    out["recipes.category.espresso"] = panel["recipes.cat.espresso"]
    out["recipes.category.coffee"] = card["values"]["coffee"]
    out["recipes.category.water"] = panel["recipes.cat.water"]

    # actions — 12 dormant button names, 8 card descriptions, 2 card
    # group labels; the rest is authored/constant below.
    buttons = entity["button"]
    for action in BUTTON_ACTIONS:
        out[f"actions.{action}.label"] = buttons[action]["name"]
    for action in DESCRIBED_ACTIONS:
        out[f"actions.{action}.description"] = (
            card["maintenance"]["actions"][action]["desc"]
        )
    out["actions._groups.cleaning"] = card["maintenance"]["groups"]["cleaning"]
    out["actions._groups.filter"] = card["maintenance"]["groups"]["filter"]

    out.update(CONSTANTS)
    out.update(zip(_AUTHORED_KEYS, AUTHORED[locale], strict=True))
    if locale == "en":
        out.update(EN_OVERRIDES)

    # ------------------------------------------------------------------
    # v3 (0.93): settings domain (§9.1.4)
    # ------------------------------------------------------------------
    # settings.<setting>.label — Melitta ×29 from the translated dormant
    # blocks; Nivona-only tokens en-only (anchored English, see docstring).
    for token, (domain, key) in MELITTA_SETTING_LABELS.items():
        out[f"settings.{token}.label"] = entity[domain][key]["name"]
    if locale == "en":
        for token, (domain, key) in NIVONA_SETTING_LABELS.items():
            out[f"settings.{token}.label"] = entity[domain][key]["name"]

    # settings.<setting>.levels.* — card bundles, re-keyed numeric→token.
    card_levels = card["settings"]["levels"]
    for flat_key, (setting, numeric) in CARD_LEVEL_KEYS.items():
        out[flat_key] = card_levels[setting][numeric]

    # ------------------------------------------------------------------
    # v3 (0.93): sommelier domain (§9.2.5)
    # ------------------------------------------------------------------
    # 24 tokens ×29 from the panel bundles (re-keyed family names).
    for family, (panel_prefix, tokens) in PANEL_SOMMELIER.items():
        for token in tokens:
            out[f"sommelier.{family}.{token}"] = (
                panel[f"sommelier.{panel_prefix}.{token}"]
            )

    # PWA port (en/de/ru): roast/bean_type/origin/mode + the 6 setting
    # descriptions; the other 26 locales use the authored table instead.
    if locale in PWA_LOCALES:
        app = load_app(app_repo, locale)
        for flat_key, app_key in PWA_SOMMELIER.items():
            out[flat_key] = app[app_key]
        for flat_key, app_key in PWA_SETTING_DESCRIPTIONS.items():
            out[flat_key] = app[app_key]
    else:
        out.update(
            zip(_AUTHORED_SOMMELIER_KEYS, AUTHORED_SOMMELIER[locale],
                strict=True)
        )

    # Newly authored ×29 (shared level tier, settings groups,
    # save_directkey label, singular extras-kind labels).
    out.update(zip(_AUTHORED_V3_KEYS, AUTHORED_V3[locale], strict=True))
    return out


def main() -> int:
    """Seed all 29 ui_strings/<locale>.json files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--card-repo",
        type=Path,
        default=REPO_ROOT.parent / "melitta-barista-card",
        help="path to the melitta-barista-card checkout",
    )
    parser.add_argument(
        "--app-repo",
        type=Path,
        default=REPO_ROOT.parent / "melitta-barista-app",
        help="path to the melitta-barista-app (PWA) checkout",
    )
    args = parser.parse_args()

    locales = sorted(
        path.stem for path in (COMPONENT / "translations").glob("*.json")
    )
    missing_authored = (set(locales) - set(AUTHORED)) | (
        set(locales) - set(PWA_LOCALES) - set(AUTHORED_SOMMELIER)
    ) | (set(locales) - set(AUTHORED_V3))
    if missing_authored:
        print(f"No authored strings for: {sorted(missing_authored)}",
              file=sys.stderr)
        return 1

    OUT_DIR.mkdir(exist_ok=True)
    for locale in locales:
        data = build_locale(locale, args.card_repo, args.app_repo)
        path = OUT_DIR / f"{locale}.json"
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {path.relative_to(REPO_ROOT)} ({len(data)} keys)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
