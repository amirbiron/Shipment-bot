"""
State Definitions for Sender and Courier Flows
"""
from enum import Enum


class SenderState(str, Enum):
    """States for sender conversation flow"""

    # Initial states
    INITIAL = "INITIAL"
    NEW = "SENDER.NEW"

    # Registration flow
    REGISTER_COLLECT_NAME = "SENDER.REGISTER.COLLECT_NAME"
    REGISTER_COLLECT_PHONE = "SENDER.REGISTER.COLLECT_PHONE"

    # Main menu
    MENU = "SENDER.MENU"

    # Delivery creation flow
    DELIVERY_COLLECT_PICKUP = "SENDER.DELIVERY.COLLECT_PICKUP"
    DELIVERY_COLLECT_PICKUP_CONTACT = "SENDER.DELIVERY.COLLECT_PICKUP_CONTACT"
    DELIVERY_COLLECT_PICKUP_NOTES = "SENDER.DELIVERY.COLLECT_PICKUP_NOTES"
    DELIVERY_COLLECT_DROPOFF_MODE = "SENDER.DELIVERY.COLLECT_DROPOFF_MODE"
    DELIVERY_COLLECT_DROPOFF_ADDRESS = "SENDER.DELIVERY.COLLECT_DROPOFF_ADDRESS"
    DELIVERY_COLLECT_DROPOFF_CONTACT = "SENDER.DELIVERY.COLLECT_DROPOFF_CONTACT"
    DELIVERY_COLLECT_DROPOFF_NOTES = "SENDER.DELIVERY.COLLECT_DROPOFF_NOTES"
    DELIVERY_CONFIRM = "SENDER.DELIVERY.CONFIRM"

    # History view
    VIEW_DELIVERIES = "SENDER.VIEW_DELIVERIES"


class CourierState(str, Enum):
    """States for courier conversation flow"""

    # Initial states
    INITIAL = "INITIAL"
    NEW = "COURIER.NEW"

    # Registration flow
    REGISTER_COLLECT_NAME = "COURIER.REGISTER.COLLECT_NAME"
    REGISTER_COLLECT_VEHICLE = "COURIER.REGISTER.COLLECT_VEHICLE"

    # Main menu
    MENU = "COURIER.MENU"

    # Delivery capture
    VIEW_AVAILABLE = "COURIER.VIEW_AVAILABLE"
    CAPTURE_CONFIRM = "COURIER.CAPTURE_CONFIRM"

    # Active deliveries
    VIEW_ACTIVE = "COURIER.VIEW_ACTIVE"
    MARK_DELIVERED = "COURIER.MARK_DELIVERED"

    # Wallet
    VIEW_WALLET = "COURIER.VIEW_WALLET"


# State transitions mapping
SENDER_TRANSITIONS = {
    # Allow INITIAL to go directly to REGISTER_COLLECT_NAME (shortcut) or through NEW
    SenderState.INITIAL: [SenderState.NEW, SenderState.REGISTER_COLLECT_NAME],
    SenderState.NEW: [SenderState.REGISTER_COLLECT_NAME],
    # Allow skipping phone collection if name is sufficient for registration
    SenderState.REGISTER_COLLECT_NAME: [SenderState.REGISTER_COLLECT_PHONE, SenderState.MENU],
    SenderState.REGISTER_COLLECT_PHONE: [SenderState.MENU],
    SenderState.MENU: [
        SenderState.DELIVERY_COLLECT_PICKUP,
        SenderState.VIEW_DELIVERIES
    ],
    SenderState.DELIVERY_COLLECT_PICKUP: [SenderState.DELIVERY_COLLECT_PICKUP_CONTACT],
    SenderState.DELIVERY_COLLECT_PICKUP_CONTACT: [SenderState.DELIVERY_COLLECT_PICKUP_NOTES],
    SenderState.DELIVERY_COLLECT_PICKUP_NOTES: [SenderState.DELIVERY_COLLECT_DROPOFF_MODE],
    SenderState.DELIVERY_COLLECT_DROPOFF_MODE: [SenderState.DELIVERY_COLLECT_DROPOFF_ADDRESS],
    SenderState.DELIVERY_COLLECT_DROPOFF_ADDRESS: [SenderState.DELIVERY_COLLECT_DROPOFF_CONTACT],
    SenderState.DELIVERY_COLLECT_DROPOFF_CONTACT: [SenderState.DELIVERY_COLLECT_DROPOFF_NOTES],
    SenderState.DELIVERY_COLLECT_DROPOFF_NOTES: [SenderState.DELIVERY_CONFIRM],
    SenderState.DELIVERY_CONFIRM: [SenderState.MENU],
    SenderState.VIEW_DELIVERIES: [SenderState.MENU],
}

COURIER_TRANSITIONS = {
    CourierState.INITIAL: [CourierState.NEW],
    CourierState.NEW: [CourierState.REGISTER_COLLECT_NAME],
    CourierState.REGISTER_COLLECT_NAME: [CourierState.REGISTER_COLLECT_VEHICLE],
    CourierState.REGISTER_COLLECT_VEHICLE: [CourierState.MENU],
    CourierState.MENU: [
        CourierState.VIEW_AVAILABLE,
        CourierState.VIEW_ACTIVE,
        CourierState.VIEW_WALLET
    ],
    CourierState.VIEW_AVAILABLE: [CourierState.CAPTURE_CONFIRM, CourierState.MENU],
    CourierState.CAPTURE_CONFIRM: [CourierState.MENU],
    CourierState.VIEW_ACTIVE: [CourierState.MARK_DELIVERED, CourierState.MENU],
    CourierState.MARK_DELIVERED: [CourierState.MENU],
    CourierState.VIEW_WALLET: [CourierState.MENU],
}
