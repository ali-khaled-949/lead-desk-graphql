"""Synthetic data for the demo and tests. Names are made up."""
from .db import Database

AGENTS = [("Ana Agent", "AGENT"), ("Ben Agent", "AGENT"), ("Mia Manager", "MANAGER")]
STATES = ["CA", "TX", "FL", "AZ", "NV"]
PRODUCTS = ["Term Life", "Whole Life", "Final Expense", "Medicare Supplement"]


def demo_database(open_leads=30, ana=10, ben=5):
    db = Database()
    leads = []
    for i in range(open_leads):
        leads.append((f"Open Lead {i + 1}", STATES[i % 5], PRODUCTS[i % 4], "NEW", None))
    for i in range(ana):
        leads.append((f"Ana Lead {i + 1}", STATES[i % 5], PRODUCTS[i % 4], ["CONTACTED", "QUOTED"][i % 2], 1))
    for i in range(ben):
        leads.append((f"Ben Lead {i + 1}", STATES[i % 5], PRODUCTS[i % 4], "CONTACTED", 2))
    db.seed(AGENTS, leads)
    return db
