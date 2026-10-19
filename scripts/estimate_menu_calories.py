"""Build reviewable, approximate per-portion nutrition estimates for imported menus.

USDA FoodData Central, FNDDS 2021-2023 (October 2024 release):
https://fdc.nal.usda.gov/download-datasets/

The restaurants have not declared these calorie numbers. Component proportions are
assumptions, not recipes. The source menu weight is used where available; otherwise
the assumed weight appears in the method. No allergen or ingredient-completeness
claim is made.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MENU = ROOT / "scripts/menu-data/verified-restaurants.json"
OUTPUT = ROOT / "scripts/menu-data/calorie-estimates.json"
MACRO_FILE = ROOT / "scripts/menu-data/fndds-macros.json"

# (FoodData Central ID, kcal per 100 g, representative FNDDS food). In particular,
# mozzarella stands in for suluguni and fried potato for fried sweet potato.
REF = {
    "greens": (2709792, 21, "салатная зелень"),
    "tomato": (2709719, 20, "томаты"),
    "cucumber": (2709784, 16, "огурцы"),
    "vegetables": (2709664, 50, "овощи"),
    "beet": (2709770, 43, "свёкла"),
    "eggplant": (2709930, 51, "баклажан"),
    "pumpkin": (2709692, 52, "тыква"),
    "mushroom": (2709938, 66, "грибы"),
    "olive": (2710088, 116, "оливки"),
    "avocado": (2709223, 160, "авокадо"),
    "oil": (2710186, 900, "масло"),
    "caesar": (2710199, 542, "соус цезарь"),
    "mayo": (2710204, 680, "майонезный соус"),
    "cream": (2705597, 343, "сливки"),
    "sour": (2705614, 196, "сметана"),
    "yogurt": (2705418, 78, "мацони/йогурт"),
    "butter": (2710155, 743, "сливочное масло"),
    "honey": (2710281, 304, "мёд/сладкий соус"),
    "cheese": (2705722, 296, "сыр (аналог моцареллы)"),
    "cheddar": (2705709, 409, "чеддер"),
    "parmesan": (2705728, 412, "пармезан"),
    "feta": (2705714, 273, "фета/рассольный сыр"),
    "brie": (2705708, 334, "камамбер/бри"),
    "ricotta": (2705750, 148, "мягкий сыр"),
    "bread": (2707598, 267, "хлеб/тесто"),
    "tortilla": (2707824, 306, "тортилья"),
    "beef": (2705824, 229, "говяжий стейк"),
    "ribeye": (2705828, 260, "рибай"),
    "braised_beef": (2705848, 251, "томлёная говядина"),
    "brisket": (2705851, 231, "брискет"),
    "lamb": (2705905, 292, "баранина"),
    "mince": (2705854, 261, "говяжий фарш"),
    "chicken": (2705956, 161, "куриное филе"),
    "thigh": (2706029, 231, "куриное бедро"),
    "turkey": (2706109, 139, "индейка"),
    "pork": (2705867, 195, "свинина"),
    "ribs": (2705894, 254, "свиные рёбра"),
    "sausage": (2706190, 325, "колбаски/чоризо"),
    "bacon": (2705885, 484, "бекон"),
    "salmon": (2706284, 188, "лосось сырой/слабосолёный"),
    "salmon_cooked": (2706286, 274, "лосось приготовленный"),
    "trout": (2706303, 216, "форель приготовленная"),
    "herring": (2706261, 245, "сельдь"),
    "tuna": (2706308, 109, "тунец сырой"),
    "shrimp": (2706362, 110, "креветки"),
    "cod": (2706241, 126, "треска"),
    "duck": (2706137, 336, "утка с кожей"),
    "rice": (2708403, 129, "рис отварной"),
    "buckwheat": (2708362, 118, "гречка"),
    "cornmeal": (2708374, 58, "кукурузная каша"),
    "pasta": (2708357, 157, "паста отварная"),
    "potato": (2709383, 93, "картофель печёный"),
    "mash": (2709492, 114, "картофельное пюре"),
    "fries": (2709456, 225, "картофель фри"),
    "sweetpotato": (2709699, 82, "батат печёный"),
    "broccoli": (2710792, 41, "брокколи"),
    "egg": (2707154, 143, "яйцо"),
    "walnut": (2707531, 730, "грецкий орех/фисташки"),
    "sugar": (2710258, 401, "сахар"),
    "custard": (2705684, 113, "крем-брюле"),
    "cheesecake": (2707861, 399, "чизкейк"),
    "tiramisu": (2705702, 353, "тирамису"),
    "cake": (2707882, 353, "бисквит/медовик"),
    "chocolate_cake": (2707871, 410, "шоколадный кекс"),
    "icecream": (2705630, 207, "мороженое"),
    "baklava": (2708044, 440, "пахлава"),
    "milk": (2705385, 61, "молоко"),
}
MACROS = json.loads(MACRO_FILE.read_text())["components"]
if set(MACROS) != set(REF) or any(MACROS[key]["fdc_id"] != value[0] for key, value in REF.items()):
    raise ValueError("FNDDS macro reference does not match recipe components")

# Relative ingredient masses in a representative recipe. Water-rich broth is
# represented by greens (21 kcal/100g); recipe ratios are deliberately explicit.
RECIPES = {
    "caesar_chicken": {"greens": 38, "chicken": 30, "bread": 12, "parmesan": 7, "caesar": 13},
    "caesar_shrimp": {"greens": 38, "shrimp": 30, "bread": 12, "parmesan": 7, "caesar": 13},
    "olivier_salmon": {"potato": 40, "salmon": 20, "vegetables": 15, "egg": 10, "mayo": 15},
    "olivier_chicken": {"potato": 40, "chicken": 20, "vegetables": 15, "egg": 10, "mayo": 15},
    "salad_duck": {"greens": 35, "duck": 28, "vegetables": 22, "oil": 8, "honey": 7},
    "salad_village": {"tomato": 30, "cucumber": 30, "vegetables": 20, "sour": 15, "oil": 5},
    "greek": {"tomato": 32, "cucumber": 30, "feta": 20, "olive": 10, "oil": 8},
    "eggplant_fried": {"eggplant": 50, "bread": 20, "oil": 12, "tomato": 13, "walnut": 5},
    "salad_shrimp": {"greens": 35, "shrimp": 30, "vegetables": 20, "parmesan": 8, "oil": 7},
    "salad_seafood": {"greens": 32, "shrimp": 26, "cod": 18, "vegetables": 16, "oil": 8},
    "tartare_salmon": {"salmon": 65, "avocado": 15, "vegetables": 15, "oil": 5},
    "tartare_tuna": {"tuna": 65, "avocado": 15, "vegetables": 15, "oil": 5},
    "tartare_beef": {"beef": 68, "bread": 15, "vegetables": 12, "oil": 5},
    "bruschetta_salmon": {"bread": 48, "salmon": 30, "ricotta": 15, "oil": 7},
    "bruschetta_beef": {"bread": 48, "beef": 30, "ricotta": 15, "oil": 7},
    "cheese_plate": {"cheese": 65, "cheddar": 15, "walnut": 10, "honey": 10},
    "vegetable_set": {"tomato": 30, "cucumber": 30, "vegetables": 25, "sour": 15},
    "olives": {"olive": 100},
    "herring": {"herring": 45, "potato": 35, "oil": 5, "vegetables": 15},
    "fried_shrimp": {"shrimp": 48, "bread": 25, "oil": 15, "mayo": 12},
    "fried_chicken": {"chicken": 48, "bread": 25, "oil": 15, "mayo": 12},
    "fried_chicken_pdf": {"chicken": 50, "bread": 25, "oil": 7, "mayo": 8, "tomato": 10},
    "cheese_sticks": {"cheese": 60, "bread": 20, "oil": 12, "honey": 8},
    "beef_steak": {"beef": 80, "vegetables": 15, "oil": 5},
    "ribeye_steak": {"ribeye": 80, "vegetables": 15, "oil": 5},
    "salmon_steak": {"salmon_cooked": 60, "vegetables": 22, "mushroom": 13, "oil": 5},
    "buckwheat_shrimp": {"buckwheat": 55, "shrimp": 30, "vegetables": 10, "oil": 5},
    "chicken_bulgur": {"thigh": 45, "rice": 40, "vegetables": 10, "oil": 5},
    "chicken_steak": {"chicken": 75, "vegetables": 20, "oil": 5},
    "schnitzel": {"chicken": 60, "bread": 22, "oil": 13, "vegetables": 5},
    "chicken_tomato": {"thigh": 65, "tomato": 27, "oil": 8},
    "turkey_mash": {"turkey": 45, "mash": 45, "cream": 7, "oil": 3},
    "beef_mash": {"braised_beef": 45, "mash": 45, "cream": 7, "oil": 3},
    "beef_rice": {"beef": 40, "rice": 45, "vegetables": 10, "oil": 5},
    "ribs_beef": {"brisket": 70, "vegetables": 20, "honey": 10},
    "brisket_mash": {"brisket": 45, "mash": 45, "vegetables": 5, "oil": 5},
    "stroganoff": {"beef": 35, "mash": 40, "cream": 15, "mushroom": 7, "oil": 3},
    "grain_meat": {"rice": 40, "beef": 35, "vegetables": 20, "oil": 5},
    "pasta_salmon": {"pasta": 55, "salmon_cooked": 25, "cream": 13, "parmesan": 5, "oil": 2},
    "pasta_chicken": {"pasta": 55, "chicken": 27, "vegetables": 10, "oil": 5, "parmesan": 3},
    "pasta_chicken_shrimp": {"pasta": 55, "chicken": 16, "shrimp": 16, "vegetables": 8, "oil": 5},
    "pasta_carbonara": {"pasta": 58, "bacon": 14, "egg": 10, "parmesan": 10, "cream": 8},
    "pasta_beef": {"pasta": 55, "beef": 25, "cream": 12, "parmesan": 5, "oil": 3},
    "burger": {"bread": 35, "mince": 35, "cheddar": 12, "vegetables": 8, "mayo": 10},
    "burger_brisket": {"bread": 35, "brisket": 38, "cheddar": 10, "vegetables": 8, "mayo": 9},
    "burger_brie": {"bread": 34, "mince": 35, "brie": 13, "vegetables": 8, "mayo": 10},
    "burger_chicken": {"bread": 35, "chicken": 28, "cheddar": 10, "vegetables": 9, "mayo": 10, "oil": 8},
    "burger_shrimp": {"bread": 38, "shrimp": 30, "vegetables": 12, "mayo": 12, "oil": 8},
    "pizza_cheese": {"bread": 55, "cheese": 30, "tomato": 12, "oil": 3},
    "pizza_meat": {"bread": 48, "cheese": 25, "sausage": 18, "tomato": 6, "oil": 3},
    "pizza_caesar": {"bread": 45, "cheese": 24, "chicken": 18, "caesar": 8, "tomato": 5},
    "pizza_four_cheese": {"bread": 45, "cheese": 25, "cheddar": 20, "parmesan": 7, "oil": 3},
    "borscht": {"vegetables": 45, "beef": 13, "potato": 15, "sour": 7, "greens": 20},
    "chicken_soup": {"greens": 61, "chicken": 18, "pasta": 10, "vegetables": 10, "oil": 1},
    "tom_yum": {"greens": 38, "shrimp": 25, "cod": 12, "cream": 13, "vegetables": 10, "oil": 2},
    "pumpkin_soup": {"pumpkin": 52, "greens": 23, "shrimp": 12, "cream": 10, "oil": 3},
    "mushroom_soup": {"mushroom": 45, "greens": 30, "cream": 20, "oil": 5},
    "cheese_soup": {"greens": 35, "potato": 20, "cheese": 20, "cream": 15, "bread": 10},
    "tiramisu": {"tiramisu": 100},
    "fondant": {"chocolate_cake": 65, "icecream": 25, "honey": 10},
    "cheesecake": {"cheesecake": 100},
    "honeycake": {"cake": 65, "sour": 25, "honey": 10},
    "khinkali_beef": {"bread": 42, "braised_beef": 48, "vegetables": 8, "oil": 2},
    "khinkali_lamb": {"bread": 42, "lamb": 45, "ribs": 8, "vegetables": 5},
    "khinkali_pork": {"bread": 42, "mince": 25, "pork": 28, "vegetables": 5},
    "khinkali_vegetable": {"bread": 43, "vegetables": 40, "mushroom": 12, "oil": 5},
    "khinkali_duck": {"bread": 42, "duck": 48, "vegetables": 10},
    "khinkali_salmon": {"bread": 42, "salmon": 48, "vegetables": 10},
    "khinkali_mushroom": {"bread": 43, "mushroom": 42, "vegetables": 10, "oil": 5},
    "khinkali_cheese": {"bread": 40, "cheese": 52, "sour": 8},
    "salad_lamb": {"greens": 35, "lamb": 33, "vegetables": 22, "oil": 10},
    "seafood_cream": {"shrimp": 33, "cod": 25, "cream": 27, "vegetables": 10, "oil": 5},
    "creme_brulee": {"custard": 45, "cream": 25, "cheese": 15, "sugar": 15},
    "elardzhi": {"cornmeal": 45, "cheese": 35, "milk": 10, "butter": 10},
    "yogurt_fig": {"yogurt": 50, "honey": 12, "walnut": 18, "cake": 20},
    "phali": {"beet": 65, "walnut": 28, "oil": 7},
    "eggplant_cheese": {"eggplant": 55, "cheese": 30, "oil": 15},
    "salad_cheese": {"greens": 35, "cheese": 35, "vegetables": 20, "oil": 10},
    "salad_salmon": {"greens": 35, "salmon": 30, "vegetables": 25, "oil": 10},
    "salad_chicken": {"greens": 35, "chicken": 35, "vegetables": 20, "oil": 10},
    "chicken_cream": {"thigh": 55, "cream": 30, "walnut": 10, "oil": 5},
    "cheesy_mash": {"mash": 55, "cheese": 30, "butter": 10, "cream": 5},
    "adjika": {"tomato": 75, "vegetables": 20, "oil": 5},
    "tkemali": {"vegetables": 75, "sugar": 15, "oil": 10},
    "sour_sauce": {"sour": 95, "greens": 5},
    "sweet_sauce": {"honey": 50, "greens": 50},
    "tomato_sauce": {"tomato": 80, "vegetables": 15, "oil": 5},
    "tzatziki": {"yogurt": 70, "cucumber": 25, "oil": 5},
    "sour": {"sour": 100},
    "yogurt": {"yogurt": 100},
    "churchkhela": {"walnut": 45, "sugar": 35, "vegetables": 20},
    "baklava": {"baklava": 100},
    "pear_pie": {"cake": 45, "cheesecake": 30, "vegetables": 20, "butter": 5},
    "vitello": {"beef": 55, "mayo": 25, "greens": 15, "oil": 5},
    "garlic_toast": {"bread": 68, "cheese": 12, "oil": 12, "mayo": 8},
    "chicken_wings": {"thigh": 75, "honey": 12, "mayo": 10, "oil": 3},
    "avocado_shrimp": {"avocado": 45, "shrimp": 35, "greens": 10, "oil": 10},
    "meat_plate": {"sausage": 35, "pork": 25, "beef": 25, "bread": 10, "vegetables": 5},
    "carpaccio": {"beef": 70, "parmesan": 10, "greens": 15, "oil": 5},
    "cheese_croquettes": {"cheddar": 55, "bread": 20, "oil": 15, "mayo": 10},
    "buratta_salad": {"cheese": 32, "eggplant": 25, "tomato": 25, "oil": 13, "walnut": 5},
    "green_salad": {"greens": 45, "avocado": 25, "vegetables": 20, "oil": 10},
    "bull_tasty_salad": {"mince": 35, "cheddar": 10, "greens": 25, "vegetables": 15, "bread": 8, "mayo": 7},
    "pumpkin_feta": {"pumpkin": 45, "feta": 25, "greens": 20, "oil": 10},
    "salad_beef": {"beef": 35, "greens": 30, "vegetables": 25, "oil": 10},
    "tomato_cheese": {"tomato": 50, "cheese": 35, "greens": 8, "oil": 7},
    "vitamin_salad": {"vegetables": 65, "greens": 15, "oil": 10, "sugar": 10},
    "beef_cheese": {"mince": 58, "cheese": 22, "cream": 12, "oil": 8},
    "turkey_grill": {"turkey": 72, "vegetables": 20, "oil": 8},
    "chicken_kebab": {"chicken": 70, "bread": 18, "vegetables": 9, "oil": 3},
    "pork_kebab": {"pork": 70, "bread": 18, "vegetables": 9, "oil": 3},
    "beef_kebab": {"beef": 70, "bread": 18, "vegetables": 9, "oil": 3},
    "pork_medallions": {"pork": 68, "bacon": 12, "bread": 10, "cream": 10},
    "sausage_plate": {"sausage": 60, "potato": 25, "vegetables": 15},
    "kebab_beef": {"mince": 55, "bread": 30, "vegetables": 10, "oil": 5},
    "kebab_turkey": {"turkey": 55, "bread": 30, "vegetables": 10, "oil": 5},
    "quesadilla": {"tortilla": 35, "mince": 30, "cheddar": 15, "vegetables": 10, "mayo": 10},
    "chicken_broth": {"greens": 66, "chicken": 15, "pasta": 8, "egg": 6, "vegetables": 5},
    "gazpacho": {"tomato": 65, "vegetables": 20, "cheese": 10, "oil": 5},
    "okroshka": {"yogurt": 45, "vegetables": 25, "brisket": 20, "egg": 7, "mayo": 3},
    "broccoli": {"broccoli": 88, "oil": 7, "greens": 5},
    "grilled_vegetables": {"vegetables": 85, "oil": 15},
    "rice_vegetables": {"rice": 68, "vegetables": 25, "oil": 7},
    "fries_sauce": {"fries": 85, "mayo": 15},
    "fries_cheese": {"fries": 75, "parmesan": 10, "mayo": 15},
    "mash_side": {"mash": 95, "butter": 5},
    "pork_ribs": {"ribs": 75, "mayo": 12, "honey": 13},
    "thai_beef": {"beef": 35, "rice": 37, "vegetables": 20, "oil": 5, "walnut": 3},
    "beef_cutlets": {"mince": 45, "mash": 45, "cream": 7, "oil": 3},
    "beef_fries": {"braised_beef": 48, "fries": 38, "mayo": 10, "vegetables": 4},
    "pistachio_roll": {"cake": 48, "cream": 30, "walnut": 17, "sugar": 5},
    "icecream": {"icecream": 100},
    "panna_cotta": {"cream": 52, "yogurt": 25, "sugar": 15, "vegetables": 8},
    "trout_steak": {"trout": 80, "greens": 15, "oil": 5},
    "pasta_bolognese": {"pasta": 50, "mince": 25, "tomato": 12, "parmesan": 6, "bacon": 4, "oil": 3},
    "tartare_trout": {"trout": 50, "avocado": 20, "vegetables": 25, "oil": 5},
    "shrimp_pilpil": {"shrimp": 45, "bread": 25, "butter": 12, "tomato": 15, "oil": 3},
    "pasta_cheek": {"pasta": 50, "braised_beef": 27, "cream": 13, "parmesan": 5, "vegetables": 5},
    "pork_knuckle": {"pork": 72, "vegetables": 22, "honey": 6},
    "duck_sweetpotato": {"duck": 45, "sweetpotato": 40, "greens": 10, "oil": 5},
    "brisket_sandwich": {"bread": 38, "brisket": 34, "cheddar": 10, "vegetables": 10, "mayo": 8},
    "brisket_plate": {"brisket": 75, "vegetables": 20, "mayo": 5},
}

# One weight (g) and recipe per published item, in verified-restaurants.json order.
# Barvikha portions are assumed because the captured menu does not state them.
BARVIKHA = """
240 caesar_shrimp
240 caesar_chicken
220 olivier_salmon
220 olivier_chicken
220 salad_duck
220 salad_village
220 greek
240 eggplant_fried
200 salad_shrimp
240 salad_seafood
160 tartare_salmon
160 tartare_tuna
160 tartare_beef
180 bruschetta_salmon
180 bruschetta_salmon
180 bruschetta_beef
200 cheese_plate
250 vegetable_set
100 olives
100 olives
220 herring
210 fried_shrimp
210 fried_chicken
190 cheese_sticks
230 beef_steak
300 ribeye_steak
270 beef_steak
260 beef_steak
270 salmon_steak
320 buckwheat_shrimp
350 chicken_bulgur
260 chicken_steak
290 schnitzel
350 chicken_tomato
350 turkey_mash
360 beef_mash
350 beef_rice
300 ribs_beef
360 brisket_mash
360 stroganoff
350 grain_meat
350 pasta_salmon
350 pasta_chicken_shrimp
350 pasta_chicken
350 pasta_carbonara
350 pasta_beef
320 burger
300 burger
320 burger
330 burger_brie
320 burger_brisket
320 burger_chicken
300 burger
320 burger
500 pizza_meat
450 pizza_cheese
500 pizza_meat
500 pizza_caesar
500 pizza_four_cheese
500 pizza_meat
350 borscht
350 chicken_soup
400 tom_yum
350 pumpkin_soup
350 mushroom_soup
350 cheese_soup
160 tiramisu
150 fondant
160 cheesecake
160 honeycake
"""

# Hachapuri portion weights are copied from its menu description and validated
# against the source manifest below, instead of being guessed here.
HACHAPURI = """
khinkali_beef salad_lamb beef_mash seafood_cream creme_brulee elardzhi
yogurt_fig phali eggplant_cheese cheese_plate vegetable_set eggplant_cheese
salad_cheese salad_salmon salad_chicken salad_duck chicken_cream cheesy_mash
khinkali_lamb khinkali_pork khinkali_vegetable khinkali_duck khinkali_salmon
khinkali_mushroom khinkali_cheese adjika tkemali sour_sauce sweet_sauce
tomato_sauce tzatziki sour yogurt churchkhela baklava pear_pie honeycake
""".split()

# THE БЫК publishes serving weights in its PDF. Meat weights marked * are raw;
# those entries use 75% cooked yield before adding the published side weight.
BULL = """
258 beef_steak raw
258 beef_steak raw
258 beef_steak raw
258 beef_steak raw
200 vitello
300 eggplant_fried
430 fried_chicken_pdf
170 fried_shrimp
310 garlic_toast
310 chicken_wings
165 avocado_shrimp
125 meat_plate
180 cheese_plate
140 carpaccio
290 cheese_croquettes
320 pizza_meat
415 pizza_meat
300 pizza_cheese
350 pizza_four_cheese
420 pizza_caesar
290 buratta_salad
210 green_salad
240 pumpkin_feta
190 salad_beef
200 salad_shrimp
220 salad_beef
300 salad_chicken
240 tomato_cheese
210 salad_beef
240 caesar_chicken
230 caesar_shrimp
200 vitamin_salad
250 greek
200 tomato_cheese
350 bull_tasty_salad
270 beef_cheese
255 turkey_grill
240 chicken_kebab
240 pork_kebab
250 pork_medallions
245 sausage_plate
245 sausage_plate
245 sausage_plate
245 sausage_plate
340 kebab_beef
340 kebab_turkey
390 quesadilla
270 burger
280 burger_brisket
280 burger
280 burger
210 burger_shrimp
350 borscht
340 cheese_soup
460 tom_yum
470 chicken_broth
310 gazpacho
390 okroshka
225 broccoli
350 grilled_vegetables
250 rice_vegetables
270 fries_sauce
340 fries_sauce
240 fries_cheese
340 fries_sauce
310 fries_sauce
210 mash_side
270 fries_sauce
350 stroganoff
300 pork_ribs
300 pork_ribs
300 pork_ribs
390 thai_beef
350 beef_cutlets
320 beef_fries
320 pork_ribs
210 cheesecake
110 pistachio_roll
150 fondant
190 honeycake
150 icecream
180 panna_cotta
258 beef_steak raw
240 beef_kebab
340 pork_kebab raw
295 ribeye_steak raw
180 trout_steak
258 beef_steak raw
258 beef_steak raw
258 beef_steak raw
333 ribeye_steak raw
295 ribeye_steak raw
430 pasta_bolognese
200 tartare_trout
145 tartare_beef
300 shrimp_pilpil
410 pasta_cheek
390 beef_mash
440 pork_knuckle
410 duck_sweetpotato
410 brisket_sandwich
350 brisket_plate
"""


def profile_lines(block):
    return [line.split() for line in block.strip().splitlines()]


def estimate(item, grams, recipe_name, weight_note):
    recipe = RECIPES[recipe_name]
    if round(sum(recipe.values()), 8) != 100:
        raise ValueError(f"Recipe {recipe_name} does not total 100%")
    reference = []
    total = 0.0
    macro_totals = {"protein_g": 0.0, "fat_g": 0.0, "carbs_g": 0.0}
    for component, percentage in recipe.items():
        if percentage == 0:
            continue
        fdc_id, density, label = REF[component]
        component_g = grams * percentage / 100
        total += component_g * density / 100
        for nutrient in macro_totals:
            macro_totals[nutrient] += component_g * MACROS[component][nutrient] / 100
        reference.append(f"{label} {component_g:g} г (FDC {fdc_id})")
    kcal = int(round(total / 10) * 10)
    method = (
        f"Оценка на порцию: {weight_note}. Условные массы компонентов: "
        + "; ".join(reference)
        + ". Энергетическая ценность компонентов — USDA FoodData Central, FNDDS 2021–2023, "
        "ккал и БЖУ на 100 г; калории округлены до 10 ккал, БЖУ до 0,1 г. "
        "Доли ингредиентов и выход блюда не подтверждены рестораном."
    )
    if len(method) > 2000:
        raise ValueError(f"Nutrition method too long: {item['name']}")
    return {
        "calories_kcal": kcal,
        **{nutrient: round(value, 1) for nutrient, value in macro_totals.items()},
        "source": "estimated", "basis": "per_portion", "method": method,
    }


def build():
    restaurants = json.loads(MENU.read_text())
    assert [r["name"] for r in restaurants] == ["Барвиха Lounge", "Хачапури и вино", "THE БЫК"]
    profiles = [profile_lines(BARVIKHA), [[r] for r in HACHAPURI], profile_lines(BULL)]
    result = {}
    for restaurant, rows in zip(restaurants, profiles, strict=True):
        items = restaurant["items"]
        if len(items) != len(rows):
            raise ValueError(f"{restaurant['name']}: {len(items)} items, {len(rows)} estimate profiles")
        estimates = {}
        for item, row in zip(items, rows, strict=True):
            if restaurant["name"] == "Хачапури и вино":
                recipe = row[0]
                grams = item["portion_weight_g"]
                note = f"масса {grams:g} г указана в меню"
            else:
                grams, recipe = float(row[0]), row[1]
                if restaurant["name"] == "Барвиха Lounge":
                    note = f"масса {grams:g} г предположена по типичной ресторанной порции"
                elif len(row) == 3 and row[2] == "raw":
                    note = f"расчётная съедобная масса {grams:g} г; мясо в меню взвешено сырым, принят выход 75%"
                elif item["name"] in {"Классический борщ", "Свиная рулька"}:
                    note = f"масса {grams:g} г предположена; точный вес не удалось подтвердить по PDF"
                else:
                    note = f"масса около {grams:g} г по меню PDF (включая гарнир/соус, если указан)"
            if item["name"] in estimates:
                raise ValueError(f"Duplicate menu name: {item['name']}")
            estimates[item["name"]] = estimate(item, grams, recipe, note)
        result[restaurant["name"]] = estimates
    return result


if __name__ == "__main__":
    data = build()
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    for restaurant, items in data.items():
        values = [x["calories_kcal"] for x in items.values()]
        print(f"{restaurant}: {len(items)} estimates, {min(values)}–{max(values)} kcal")
