from uagents import Agent, Context, Model

# -----------------------------
# Create Agent
# -----------------------------
agent = Agent(
    name="logistics_economist",
    seed=os.getenv("AGENT_SEED"),
    endpoint="http://127.0.0.1:8001/submit",
    port=8001,
)

# -----------------------------
# Input Model
# -----------------------------
class CargoRequest(Model):
    description: str
    quantity: int
    weight_kg: float
    length_cm: float
    width_cm: float
    height_cm: float
    distance_miles: float


# -----------------------------
# Output Model
# -----------------------------
class CargoResponse(Model):
    cargo_value: float
    volume_m3: float
    density: float
    shipping_class: str
    fuel_cost: float
    driver_cost: float
    vehicle_cost: float
    baseline_transport_cost: float


# -----------------------------
# Cargo Value Lookup
# -----------------------------
ITEM_VALUES = {
    "iphone": 1100,
    "laptop": 1200,
    "television": 700,
    "steel": 900,
    "books": 20,
    "clothing": 40,
    "furniture": 350,
}


def estimate_value(description: str, quantity: int):
    description = description.lower()

    for item, value in ITEM_VALUES.items():
        if item in description:
            return value * quantity

    return quantity * 100


# -----------------------------
# Message Handler
# -----------------------------
@agent.on_message(model=CargoRequest)
async def calculate(ctx: Context, sender: str, msg: CargoRequest):

    volume = (
        msg.length_cm
        * msg.width_cm
        * msg.height_cm
    ) / 1_000_000

    density = msg.weight_kg / volume

    if density > 300:
        shipping = "Heavy"
    elif density > 100:
        shipping = "Medium"
    else:
        shipping = "Light"

    cargo_value = estimate_value(
        msg.description,
        msg.quantity,
    )

    fuel_price = 4.25
    truck_mpg = 6.5
    driver_wage = 35
    avg_speed = 55
    vehicle_cost_per_mile = 1.10

    gallons = msg.distance_miles / truck_mpg
    fuel_cost = gallons * fuel_price

    hours = msg.distance_miles / avg_speed
    driver_cost = hours * driver_wage

    vehicle_cost = msg.distance_miles * vehicle_cost_per_mile

    total = fuel_cost + driver_cost + vehicle_cost

    response = CargoResponse(
        cargo_value=round(cargo_value, 2),
        volume_m3=round(volume, 2),
        density=round(density, 2),
        shipping_class=shipping,
        fuel_cost=round(fuel_cost, 2),
        driver_cost=round(driver_cost, 2),
        vehicle_cost=round(vehicle_cost, 2),
        baseline_transport_cost=round(total, 2),
    )

    await ctx.send(sender, response)


if __name__ == "__main__":
    agent.run()