from route_logic import calculate_route
from routing_models import EconData, Item, RoutingRequest, ShipmentRequest

request = RoutingRequest(
    shipment=ShipmentRequest(
        origin={"country": "CN", "state": "Guangdong", "city": "Shenzhen"},
        destination={"country": "US", "state": "TX", "city": "Austin"},
        items=[Item(name="Electronics", quantity=10, category="electronics")],
        total_weight_kg=800,
        total_volume_cbm=4.2,
        timeframe="COST",
        declared_value_usd=5000,
    ),
    econ=EconData(
        transport_preference="EITHER",
        is_high_value=True,
        is_luxury=False,
        base_entry_tax_usd=350,
    ),
)

print(calculate_route(request).model_dump_json(indent=2))
