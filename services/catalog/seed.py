"""Explicit synthetic fixtures: no real restaurant, review or nutrition claim."""
from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5
from sqlalchemy import select

from .models import DishRow, EvidenceRow, ImportRow, MenuItemRow, MenuVersionRow, RestaurantRow, ReviewRow, now

DEMO_NOTICE = "Демонстрационные учебные данные. Ресторан, отзывы, цены и КБЖУ вымышлены."
ALLERGENS = ["wheat", "milk", "egg", "peanut", "tree_nut", "fish", "shellfish", "soy", "sesame"]


def demo_id(key):
    return str(uuid5(NAMESPACE_URL, "gastrogid:demo:" + key))


# name, ingredients, RUB price, portion grams, kcal, contains, tags
MENUS = [
    ("Тбилиси", "georgian", [
        ("Хинкали с говядиной, 4 шт.", ["пшеничная мука", "говядина", "лук", "вода"], 750, 360, 620, ["wheat"], ["main", "mild", "dumplings"]),
        ("Хинкали с грибами, 4 шт.", ["пшеничная мука", "шампиньоны", "лук", "вода"], 620, 320, 480, ["wheat"], ["main", "mild", "vegetarian", "dumplings"]),
        ("Хачапури по-аджарски", ["пшеничная мука", "сыр", "яйцо", "масло"], 690, 400, 900, ["wheat", "milk", "egg"], ["main", "mild", "cheese"]),
        ("Лобио", ["фасоль", "лук", "кинза", "масло"], 420, 250, 330, [], ["main", "mild", "vegan"]),
        ("Пхали со шпинатом", ["шпинат", "грецкий орех", "чеснок"], 390, 150, 280, ["tree_nut"], ["starter", "mild", "vegan"]),
        ("Шашлык из курицы", ["курица", "лук", "соль"], 650, 220, 380, [], ["main", "mild", "high_protein"]),
        ("Овощи на гриле", ["баклажан", "перец", "кабачок", "масло"], 350, 200, 180, [], ["side", "mild", "vegan"]),
        ("Салат с помидорами", ["помидор", "огурец", "лук", "масло"], 320, 200, 140, [], ["salad", "mild", "vegan"]),
        ("Суп харчо", ["говядина", "рис", "помидор", "чили"], 490, 300, 360, [], ["soup", "spicy"]),
        ("Сырная тарелка к пиву", ["сыр сулугуни", "сыр имеретинский"], 590, 180, 540, ["milk"], ["starter", "mild", "beer_snack"]),
        ("Чурчхела", ["грецкий орех", "виноградный сок", "пшеничная мука"], 240, 80, 300, ["tree_nut", "wheat"], ["dessert", "mild"]),
        ("Лимонад тархун", ["вода", "сахар", "тархун"], 190, 300, 120, [], ["drink", "mild", "vegan"]),
    ]),
    ("Зелёная кухня", "vegetarian", [
        ("Боул с киноа", ["киноа", "авокадо", "нут", "помидор"], 580, 300, 450, [], ["main", "mild", "vegan"]),
        ("Чечевичный суп", ["чечевица", "морковь", "лук"], 360, 300, 280, [], ["soup", "mild", "vegan"]),
        ("Тофу с овощами", ["тофу", "брокколи", "морковь", "соевый соус"], 520, 280, 340, ["soy", "wheat"], ["main", "mild", "vegan"]),
        ("Фалафель", ["нут", "лук", "масло", "кунжут"], 390, 180, 420, ["sesame"], ["starter", "mild", "vegan", "beer_snack"]),
        ("Хумус", ["нут", "тахини", "лимон", "масло"], 290, 150, 300, ["sesame"], ["starter", "mild", "vegan"]),
        ("Салат из свёклы", ["свёкла", "руккола", "масло"], 340, 200, 170, [], ["salad", "mild", "vegan"]),
        ("Гречка с грибами", ["гречка", "шампиньоны", "лук"], 420, 250, 290, [], ["main", "mild", "vegan"]),
        ("Паста с томатами", ["пшеничная паста", "помидор", "базилик"], 490, 280, 410, ["wheat"], ["main", "mild", "vegan"]),
        ("Смузи манго", ["манго", "банан", "вода"], 280, 250, 180, [], ["drink", "mild", "vegan"]),
        ("Йогурт с ягодами", ["йогурт", "малина", "мёд"], 250, 180, 190, ["milk"], ["dessert", "mild", "vegetarian"]),
        ("Ореховый батончик", ["арахис", "финики", "миндаль"], 180, 60, 240, ["peanut", "tree_nut"], ["dessert", "mild", "vegan"]),
        ("Картофель запечённый", ["картофель", "масло", "соль"], 240, 200, 260, [], ["side", "mild", "vegan"]),
    ]),
    ("Бирхаус", "european", [
        ("Куриные крылья к пиву", ["курица", "чили", "мёд"], 620, 300, 650, [], ["main", "spicy", "beer_snack"]),
        ("Картофель фри", ["картофель", "масло", "соль"], 260, 150, 440, [], ["side", "mild", "vegan", "beer_snack"]),
        ("Луковые кольца", ["лук", "пшеничная мука", "яйцо", "масло"], 340, 180, 520, ["wheat", "egg"], ["starter", "mild", "beer_snack"]),
        ("Гренки чесночные", ["ржаной хлеб", "пшеничная мука", "чеснок", "масло"], 290, 140, 430, ["wheat"], ["starter", "mild", "beer_snack"]),
        ("Колбаски с капустой", ["свинина", "капуста", "соль"], 780, 320, 720, [], ["main", "mild", "beer_snack"]),
        ("Бургер с говядиной", ["говядина", "пшеничная булочка", "сыр", "лук"], 690, 300, 780, ["wheat", "milk"], ["main", "mild"]),
        ("Салат цезарь", ["курица", "салат", "сыр", "яйцо", "пшеничные сухари"], 520, 220, 410, ["wheat", "milk", "egg"], ["salad", "mild"]),
        ("Стейк говяжий", ["говядина", "масло", "соль"], 1350, 250, 560, [], ["main", "mild", "high_protein"]),
        ("Рыба с картофелем", ["треска", "картофель", "масло"], 720, 280, 480, ["fish"], ["main", "mild"]),
        ("Сырные палочки", ["сыр", "пшеничная мука", "яйцо", "масло"], 420, 180, 580, ["milk", "wheat", "egg"], ["starter", "mild", "beer_snack"]),
        ("Креветки жареные", ["креветки", "чеснок", "масло"], 850, 200, 290, ["shellfish"], ["starter", "mild", "high_protein"]),
        ("Морс ягодный", ["вода", "клюква", "сахар"], 170, 300, 110, [], ["drink", "mild"]),
    ]),
    ("Азия рядом", "asian", [
        ("Рамен с курицей", ["пшеничная лапша", "курица", "яйцо", "соевый соус"], 650, 450, 620, ["wheat", "egg", "soy"], ["main", "mild", "soup"]),
        ("Пад тай", ["рисовая лапша", "креветки", "арахис", "яйцо"], 690, 320, 640, ["shellfish", "peanut", "egg"], ["main", "mild"]),
        ("Том ям", ["креветки", "кокосовое молоко", "чили", "грибы"], 590, 350, 360, ["shellfish"], ["soup", "spicy"]),
        ("Рис с овощами", ["рис", "морковь", "горошек", "масло"], 350, 250, 340, [], ["main", "mild", "vegan"]),
        ("Гёдза с курицей", ["пшеничная мука", "курица", "капуста", "соевый соус"], 480, 200, 380, ["wheat", "soy"], ["starter", "mild", "dumplings"]),
        ("Суши с лососем", ["рис", "лосось", "нори"], 420, 140, 240, ["fish"], ["main", "mild"]),
        ("Ролл с авокадо", ["рис", "авокадо", "нори"], 380, 180, 300, [], ["main", "mild", "vegan"]),
        ("Эдамаме", ["соевые бобы", "соль"], 280, 150, 190, ["soy"], ["starter", "mild", "vegan", "beer_snack"]),
        ("Курица терияки", ["курица", "соевый соус", "сахар", "кунжут"], 620, 250, 450, ["soy", "wheat", "sesame"], ["main", "mild", "high_protein"]),
        ("Салат с огурцом", ["огурец", "кунжут", "соевый соус"], 260, 160, 140, ["soy", "wheat", "sesame"], ["salad", "mild"]),
        ("Моти", ["рисовая мука", "молоко", "сахар"], 270, 90, 220, ["milk"], ["dessert", "mild"]),
        ("Чай жасминовый", ["вода", "чай"], 160, 300, 0, [], ["drink", "mild"]),
    ]),
    ("Паста и пицца", "italian", [
        ("Пицца маргарита", ["пшеничная мука", "сыр", "помидор"], 650, 400, 880, ["wheat", "milk"], ["main", "mild", "vegetarian"]),
        ("Паста карбонара", ["пшеничная паста", "бекон", "яйцо", "сыр"], 590, 280, 680, ["wheat", "egg", "milk"], ["main", "mild"]),
        ("Паста с креветками", ["пшеничная паста", "креветки", "сливки"], 720, 300, 610, ["wheat", "shellfish", "milk"], ["main", "mild"]),
        ("Ризотто с грибами", ["рис", "грибы", "сыр", "масло"], 550, 280, 520, ["milk"], ["main", "mild", "vegetarian"]),
        ("Минестроне", ["фасоль", "кабачок", "морковь", "помидор"], 390, 300, 230, [], ["soup", "mild", "vegan"]),
        ("Капрезе", ["помидор", "моцарелла", "базилик"], 490, 220, 340, ["milk"], ["salad", "mild", "vegetarian"]),
        ("Брускетта с томатами", ["пшеничный хлеб", "помидор", "масло"], 320, 140, 280, ["wheat"], ["starter", "mild", "vegan"]),
        ("Курица с брокколи", ["курица", "брокколи", "масло"], 650, 280, 390, [], ["main", "mild", "high_protein"]),
        ("Пицца пепперони", ["пшеничная мука", "колбаса", "сыр", "чили"], 750, 420, 1050, ["wheat", "milk"], ["main", "spicy"]),
        ("Тирамису", ["маскарпоне", "яйцо", "пшеничное печенье", "кофе"], 390, 150, 430, ["wheat", "milk", "egg"], ["dessert", "mild"]),
        ("Панна-котта", ["сливки", "сахар", "желатин"], 340, 140, 320, ["milk"], ["dessert", "mild"]),
        ("Эспрессо", ["вода", "кофе"], 140, 40, 2, [], ["drink", "mild"]),
    ]),
]


def seed_demo(session):
    for r_index, (name, cuisine, dishes) in enumerate(MENUS):
        restaurant_id = demo_id(f"restaurant-{r_index}")
        if session.get(RestaurantRow, restaurant_id):
            continue
        import_id, menu_id = demo_id(f"import-{r_index}"), demo_id(f"menu-{r_index}-1")
        restaurant = RestaurantRow(id=restaurant_id, name="Демо: " + name,
            address=f"Демонстрационный адрес {r_index + 1}, Москва (вымышленное заведение)",
            latitude=55.7558 + r_index*0.004, longitude=37.6173 + r_index*0.003,
            cuisines=[cuisine], is_demo=True, active=True)
        session.add(restaurant)
        session.flush()
        source_html = [f"<html><body><p>{DEMO_NOTICE}</p>"]
        for row in dishes:
            source_html.append(f"<p>{row[0]} | {row[2]} ₽ | {row[3]} г | состав: {', '.join(row[1])}</p>")
        source_html.append("</body></html>")
        session.add(ImportRow(id=import_id, restaurant_id=restaurant_id, source_kind="html",
            filename=f"demo-menu-{r_index+1}.html", content_type="text/html; charset=utf-8",
            file_bytes="\n".join(source_html).encode(), status="published", progress_percent=100,
            revision=1, currency="RUB", published_menu_version_id=menu_id))
        session.flush()
        session.add(MenuVersionRow(id=menu_id, restaurant_id=restaurant_id, import_id=import_id,
            version=1, status="published", currency="RUB"))
        session.flush()
        restaurant.active_menu_version_id = menu_id
        for d_index, (dish_name, ingredients, price, weight, kcal, contains, tags) in enumerate(dishes):
            key = f"{r_index}-{d_index}"
            dish_id, item_id, evidence_id = demo_id("dish-"+key), demo_id("item-"+key), demo_id("evidence-"+key)
            session.add(DishRow(id=dish_id, restaurant_id=restaurant_id, name=dish_name, description=DEMO_NOTICE))
            session.flush()
            # Synthetic totals are internally consistent but explicitly estimates.
            protein = round(kcal * (0.32 if "high_protein" in tags else 0.16) / 4, 2)
            fat = round(kcal * 0.32 / 9, 2)
            carbs = round((kcal - protein*4 - fat*9)/4, 2)
            nutrition = dict(calories_kcal=kcal, protein_g=protein, fat_g=fat, carbs_g=carbs,
                basis="per_portion", source="estimated", method=DEMO_NOTICE + " Учебные значения заданы в fixture; не измерения и не оценка реального блюда.",
                evidence_ids=[evidence_id])
            unknown = r_index == 0 and d_index == 10
            if unknown:
                nutrition = dict(calories_kcal=None, protein_g=None, fat_g=None, carbs_g=None,
                    basis="per_portion", source="unknown", method=None, evidence_ids=[])
            declarations = [{"code": x, "status": "contains" if x in contains else "declared_absent", "evidence_id": evidence_id} for x in ALLERGENS]
            session.add(MenuItemRow(id=item_id, dish_id=dish_id, restaurant_id=restaurant_id,
                menu_version_id=menu_id, name=dish_name, description=DEMO_NOTICE,
                ingredients=ingredients, ingredients_complete=not unknown,
                tags=tags+[cuisine], price_minor=price*100, currency="RUB", portion_weight_g=weight,
                allergen_info_status="partial" if unknown else "complete",
                allergens=[x for x in declarations if x["status"] == "contains"] if unknown else declarations,
                nutrition=nutrition, evidence_ids=[evidence_id], is_demo=True))
            session.flush()
            session.add(EvidenceRow(id=evidence_id, restaurant_id=restaurant_id, import_id=import_id,
                menu_item_id=item_id, source_kind="html", source_reference=f"demo-menu-{r_index+1}.html",
                fragment_text=f"{DEMO_NOTICE}\n{dish_name}; цена={price} RUB; вес={weight} г; состав={ingredients}; "
                    f"демо декларации={declarations}; демо питание={nutrition}", is_demo=True))
        for review_index, (rating, text) in enumerate([(4.5, "Понравился выбор блюд и понятное меню."),
                                                       (4.0, "Есть варианты для обеда; обслуживание спокойное.")]):
            evidence_id = demo_id(f"review-evidence-{r_index}-{review_index}")
            session.add(EvidenceRow(id=evidence_id, restaurant_id=restaurant_id, source_kind="review",
                source_reference="synthetic-demo-reviews", fragment_text=DEMO_NOTICE + " " + text, is_demo=True))
            session.flush()
            session.add(ReviewRow(id=demo_id(f"review-{r_index}-{review_index}"), restaurant_id=restaurant_id,
                rating=rating, text="Демо-отзыв: " + text, source="synthetic-demo-fixture", is_demo=True, evidence_id=evidence_id))
    session.commit()
