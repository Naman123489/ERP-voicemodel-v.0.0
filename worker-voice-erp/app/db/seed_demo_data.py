
"""Seed safe demo data for Bicycle Voice ERP.

Creates missing demo records without resetting existing inventory.
Run from the project root:
    python -m app.db.seed_demo_data
"""

import os

from sqlalchemy import select

from app.core.security import hash_password
from app.db.database import SessionLocal
from app.db.models import Component, Product, RecipeItem, Worker


# Demo login. Override DEMO_WORKER_PASSWORD in your environment
# before running this script if you want a different password.
DEMO_USERNAME = "demo.worker"
DEMO_PASSWORD = os.getenv("DEMO_WORKER_PASSWORD", "DemoWorker@123")

WORKER_ID = "demo-worker-001"
PRODUCT_ID = "bicycle-standard-001"
RECIPE_VERSION = 1

# Component name, unit, initial quantity
COMPONENTS = [
    ("frame", "piece", 20),
    ("wheel", "piece", 30),
    ("handlebar", "piece", 20),
    ("seat", "piece", 20),
    ("chain", "piece", 20),
    ("pedal", "piece", 40),
    ("brake set", "set", 20),
]

# Component name, quantity required per bicycle
RECIPE = [
    ("frame", 1),
    ("wheel", 2),
    ("handlebar", 1),
    ("seat", 1),
    ("chain", 1),
    ("pedal", 2),
    ("brake set", 1),
]


def seed_demo_data():
    db = SessionLocal()

    try:
        with db.begin():
            # 1. Create the demo worker only if it does not exist.
            worker = db.scalar(
                select(Worker).where(Worker.username == DEMO_USERNAME)
            )

            if worker is None:
                worker = db.get(Worker, WORKER_ID)

                if worker is None:
                    worker = Worker(
                        id=WORKER_ID,
                        username=DEMO_USERNAME,
                        password_hash=hash_password(DEMO_PASSWORD),
                        display_name="Demo Worker",
                        active=True,
                    )
                    db.add(worker)
                    print("Created demo worker.")
                else:
                    raise ValueError(
                        f"Worker ID {WORKER_ID!r} already exists "
                        "with a different username. Resolve this conflict first."
                    )
            else:
                print("Demo worker already exists; preserved it.")

            # 2. Create missing components without resetting stock.
            components_by_name = {}

            for name, unit, initial_quantity in COMPONENTS:
                component = db.scalar(
                    select(Component).where(Component.name == name)
                )

                if component is None:
                    component_id = name.replace(" ", "-")

                    # Avoid accidentally reusing an ID belonging to
                    # a different component.
                    existing_id = db.get(Component, component_id)
                    if existing_id is not None:
                        raise ValueError(
                            f"Component ID {component_id!r} is already in use."
                        )

                    component = Component(
                        id=component_id,
                        name=name,
                        unit=unit,
                        quantity_on_hand=initial_quantity,
                    )
                    db.add(component)
                    db.flush()
                    print(f"Created component: {name}")
                else:
                    print(f"Component already exists: {name}; preserved stock.")

                components_by_name[name] = component

            # 3. Create the finished bicycle product if missing.
            product = db.scalar(
                select(Product).where(Product.id == PRODUCT_ID)
            )

            if product is None:
                product = db.scalar(
                    select(Product).where(Product.name == "Standard Bicycle")
                )

            if product is None:
                product = Product(
                    id=PRODUCT_ID,
                    name="Standard Bicycle",
                    unit="bicycle",
                    quantity_on_hand=0,
                    active=True,
                )
                db.add(product)
                db.flush()
                print("Created Standard Bicycle product.")
            else:
                print("Bicycle product already exists; preserved stock.")

            # 4. Create missing recipe rows.
            for component_name, quantity in RECIPE:
                component = components_by_name[component_name]

                recipe_item = db.get(
                    RecipeItem,
                    (product.id, component.id, RECIPE_VERSION),
                )

                if recipe_item is None:
                    db.add(
                        RecipeItem(
                            product_id=product.id,
                            component_id=component.id,
                            recipe_version=RECIPE_VERSION,
                            quantity_per_product=quantity,
                        )
                    )
                    print(f"Added recipe item: {component_name} x {quantity}")
                else:
                    print(f"Recipe item exists: {component_name}; preserved it.")

        print("\nDemo data seeded successfully.")

    except Exception:
        print(
            "\nSeeding failed. The database transaction will be rolled back."
        )
        raise

    finally:
        db.close()


if __name__ == "__main__":
    seed_demo_data()
