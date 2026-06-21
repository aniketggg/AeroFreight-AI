"""uAgents wire models for Economist agent communication."""

from uagents import Model


class EconomistRequest(Model):
    shipment_json: str


class EconomistResponse(Model):
    econ_data_json: str


class EconomistError(Model):
    error_message: str
