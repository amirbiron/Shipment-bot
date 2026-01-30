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
    PICKUP_APARTMENT = "SENDER.DELIVERY.PICKUP_APARTMENT"

    # Delivery creation flow - Dropoff address wizard
    DROPOFF_CITY = "SENDER.DELIVERY.DROPOFF_CITY"
    DROPOFF_STREET = "SENDER.DELIVERY.DROPOFF_STREET"
    DROPOFF_NUMBER = "SENDER.DELIVERY.DROPOFF_NUMBER"
    DROPOFF_APARTMENT = "SENDER.DELIVERY.DROPOFF_APARTMENT"

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
    INITIAL = "COURIER.INITIAL"
    NEW = "COURIER.NEW"

    # Registration flow [1.2]
    REGISTER_COLLECT_NAME = "COURIER.REGISTER.COLLECT_NAME"
    REGISTER_COLLECT_DOCUMENT = "COURIER.REGISTER.COLLECT_DOCUMENT"
    REGISTER_COLLECT_AREA = "COURIER.REGISTER.COLLECT_AREA"
    REGISTER_TERMS = "COURIER.REGISTER.TERMS"

    # Pending approval [1.4]
    PENDING_APPROVAL = "COURIER.PENDING_APPROVAL"

    # Main menu (after approval) [4]
    MENU = "COURIER.MENU"

    # Delivery operations [2]
    VIEW_AVAILABLE = "COURIER.VIEW_AVAILABLE"
    CAPTURE_CONFIRM = "COURIER.CAPTURE_CONFIRM"
    VIEW_ACTIVE = "COURIER.VIEW_ACTIVE"
    MARK_PICKED_UP = "COURIER.MARK_PICKED_UP"
    MARK_DELIVERED = "COURIER.MARK_DELIVERED"

    # Wallet [3]
    VIEW_WALLET = "COURIER.VIEW_WALLET"
    DEPOSIT_REQUEST = "COURIER.DEPOSIT_REQUEST"
    DEPOSIT_UPLOAD = "COURIER.DEPOSIT_UPLOAD"

    # Settings
    CHANGE_AREA = "COURIER.CHANGE_AREA"
    VIEW_HISTORY = "COURIER.VIEW_HISTORY"
    SUPPORT = "COURIER.SUPPORT"


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

    # Pickup address wizard: City -> Street -> Number -> Apartment -> Dropoff
    SenderState.PICKUP_CITY: [SenderState.PICKUP_STREET],
    SenderState.PICKUP_STREET: [SenderState.PICKUP_NUMBER],
    SenderState.PICKUP_NUMBER: [SenderState.PICKUP_APARTMENT],
    SenderState.PICKUP_APARTMENT: [SenderState.DROPOFF_CITY],

    # Dropoff address wizard: City -> Street -> Number -> Apartment -> Confirm
    SenderState.DROPOFF_CITY: [SenderState.DROPOFF_STREET],
    SenderState.DROPOFF_STREET: [SenderState.DROPOFF_NUMBER],
    SenderState.DROPOFF_NUMBER: [SenderState.DROPOFF_APARTMENT],
    SenderState.DROPOFF_APARTMENT: [SenderState.DELIVERY_CONFIRM, SenderState.MENU],

    # Confirmation -> Menu
    SenderState.DELIVERY_CONFIRM: [SenderState.MENU],
    SenderState.VIEW_DELIVERIES: [SenderState.MENU],
}

COURIER_TRANSITIONS = {
    # Registration flow
    CourierState.INITIAL: [CourierState.REGISTER_COLLECT_NAME],
    CourierState.NEW: [CourierState.REGISTER_COLLECT_NAME],
    CourierState.REGISTER_COLLECT_NAME: [CourierState.REGISTER_COLLECT_DOCUMENT],
    CourierState.REGISTER_COLLECT_DOCUMENT: [CourierState.REGISTER_COLLECT_AREA],
    CourierState.REGISTER_COLLECT_AREA: [CourierState.REGISTER_TERMS],
    CourierState.REGISTER_TERMS: [CourierState.PENDING_APPROVAL],
    CourierState.PENDING_APPROVAL: [CourierState.MENU],

    # Main menu navigation
    CourierState.MENU: [
        CourierState.VIEW_AVAILABLE,
        CourierState.VIEW_ACTIVE,
        CourierState.VIEW_WALLET,
        CourierState.CHANGE_AREA,
        CourierState.VIEW_HISTORY,
        CourierState.SUPPORT,
        CourierState.DEPOSIT_REQUEST,
    ],

    # Delivery capture
    CourierState.VIEW_AVAILABLE: [CourierState.CAPTURE_CONFIRM, CourierState.MENU],
    CourierState.CAPTURE_CONFIRM: [CourierState.VIEW_ACTIVE, CourierState.MENU],

    # Active delivery flow
    CourierState.VIEW_ACTIVE: [CourierState.MARK_PICKED_UP, CourierState.MENU],
    CourierState.MARK_PICKED_UP: [CourierState.MARK_DELIVERED, CourierState.VIEW_ACTIVE],
    CourierState.MARK_DELIVERED: [CourierState.MENU],

    # Wallet flow
    CourierState.VIEW_WALLET: [CourierState.DEPOSIT_REQUEST, CourierState.MENU],
    CourierState.DEPOSIT_REQUEST: [CourierState.DEPOSIT_UPLOAD, CourierState.MENU],
    CourierState.DEPOSIT_UPLOAD: [CourierState.VIEW_WALLET, CourierState.MENU],

    # Settings
    CourierState.CHANGE_AREA: [CourierState.MENU],
    CourierState.VIEW_HISTORY: [CourierState.MENU],
    CourierState.SUPPORT: [CourierState.MENU],
}
