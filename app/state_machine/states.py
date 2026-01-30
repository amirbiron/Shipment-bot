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

    # Delivery creation flow - Pickup address wizard
    PICKUP_CITY = "SENDER.DELIVERY.PICKUP_CITY"
    PICKUP_STREET = "SENDER.DELIVERY.PICKUP_STREET"
    PICKUP_NUMBER = "SENDER.DELIVERY.PICKUP_NUMBER"

    # Delivery creation flow - Dropoff address wizard
    DROPOFF_MODE = "SENDER.DELIVERY.DROPOFF_MODE"
    DROPOFF_CITY = "SENDER.DELIVERY.DROPOFF_CITY"
    DROPOFF_STREET = "SENDER.DELIVERY.DROPOFF_STREET"
    DROPOFF_NUMBER = "SENDER.DELIVERY.DROPOFF_NUMBER"

    # Confirmation
    DELIVERY_CONFIRM = "SENDER.DELIVERY.CONFIRM"

    # History view
    VIEW_DELIVERIES = "SENDER.VIEW_DELIVERIES"

    # Legacy states (for backwards compatibility)
    DELIVERY_COLLECT_PICKUP = "SENDER.DELIVERY.COLLECT_PICKUP"
    DELIVERY_COLLECT_PICKUP_CONTACT = "SENDER.DELIVERY.COLLECT_PICKUP_CONTACT"
    DELIVERY_COLLECT_PICKUP_NOTES = "SENDER.DELIVERY.COLLECT_PICKUP_NOTES"
    DELIVERY_COLLECT_DROPOFF_MODE = "SENDER.DELIVERY.COLLECT_DROPOFF_MODE"
    DELIVERY_COLLECT_DROPOFF_ADDRESS = "SENDER.DELIVERY.COLLECT_DROPOFF_ADDRESS"
    DELIVERY_COLLECT_DROPOFF_CONTACT = "SENDER.DELIVERY.COLLECT_DROPOFF_CONTACT"
    DELIVERY_COLLECT_DROPOFF_NOTES = "SENDER.DELIVERY.COLLECT_DROPOFF_NOTES"


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
    # Initial & Registration
    SenderState.INITIAL: [SenderState.NEW, SenderState.REGISTER_COLLECT_NAME],
    SenderState.NEW: [SenderState.REGISTER_COLLECT_NAME],
    SenderState.REGISTER_COLLECT_NAME: [SenderState.REGISTER_COLLECT_PHONE, SenderState.MENU],
    SenderState.REGISTER_COLLECT_PHONE: [SenderState.MENU],

    # Menu
    SenderState.MENU: [
        SenderState.PICKUP_CITY,  # New wizard flow
        SenderState.VIEW_DELIVERIES
    ],

    # Pickup address wizard: City -> Street -> Number -> Dropoff mode
    SenderState.PICKUP_CITY: [SenderState.PICKUP_STREET],
    SenderState.PICKUP_STREET: [SenderState.PICKUP_NUMBER],
    SenderState.PICKUP_NUMBER: [SenderState.DROPOFF_MODE],

    # Dropoff mode selection -> Dropoff wizard
    SenderState.DROPOFF_MODE: [SenderState.DROPOFF_CITY],

    # Dropoff address wizard: City -> Street -> Number -> Confirm
    SenderState.DROPOFF_CITY: [SenderState.DROPOFF_STREET],
    SenderState.DROPOFF_STREET: [SenderState.DROPOFF_NUMBER],
    SenderState.DROPOFF_NUMBER: [SenderState.DELIVERY_CONFIRM, SenderState.MENU],

    # Confirmation -> Menu
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
