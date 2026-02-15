"""
State Definitions for Sender, Courier, Dispatcher and Station Owner Flows
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

    # Delivery details flow
    DELIVERY_LOCATION = "SENDER.DELIVERY.LOCATION"  # Within city / outside city
    DELIVERY_URGENCY = "SENDER.DELIVERY.URGENCY"  # Immediate / Later
    DELIVERY_TIME = "SENDER.DELIVERY.TIME"  # Time in HH:MM (only for "later")
    DELIVERY_PRICE = "SENDER.DELIVERY.PRICE"  # Customer price (only for "later")
    DELIVERY_DESCRIPTION = "SENDER.DELIVERY.DESCRIPTION"  # Shipment description

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

    # Registration flow (KYC) [שלב 2]
    REGISTER_COLLECT_NAME = "COURIER.REGISTER.COLLECT_NAME"
    REGISTER_COLLECT_DOCUMENT = "COURIER.REGISTER.COLLECT_DOCUMENT"
    REGISTER_COLLECT_SELFIE = "COURIER.REGISTER.COLLECT_SELFIE"
    REGISTER_COLLECT_VEHICLE_CATEGORY = "COURIER.REGISTER.COLLECT_VEHICLE_CATEGORY"
    REGISTER_COLLECT_VEHICLE_PHOTO = "COURIER.REGISTER.COLLECT_VEHICLE_PHOTO"
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

    # Pickup address wizard: City -> Street -> Number -> Apartment -> Location (בתוך/מחוץ לעיר)
    SenderState.PICKUP_CITY: [SenderState.PICKUP_STREET],
    SenderState.PICKUP_STREET: [SenderState.PICKUP_NUMBER],
    SenderState.PICKUP_NUMBER: [SenderState.PICKUP_APARTMENT],
    SenderState.PICKUP_APARTMENT: [SenderState.DELIVERY_LOCATION],

    # בחירת סוג משלוח (בתוך/מחוץ לעיר) -> כתובת יעד
    SenderState.DELIVERY_LOCATION: [SenderState.DROPOFF_CITY],

    # Dropoff address wizard: City -> Street -> Number -> Apartment -> Urgency
    SenderState.DROPOFF_CITY: [SenderState.DROPOFF_STREET],
    SenderState.DROPOFF_STREET: [SenderState.DROPOFF_NUMBER],
    SenderState.DROPOFF_NUMBER: [SenderState.DROPOFF_APARTMENT],
    SenderState.DROPOFF_APARTMENT: [SenderState.DELIVERY_URGENCY, SenderState.MENU],

    # Delivery details flow
    SenderState.DELIVERY_URGENCY: [SenderState.DELIVERY_TIME, SenderState.DELIVERY_DESCRIPTION],
    SenderState.DELIVERY_TIME: [SenderState.DELIVERY_PRICE],
    SenderState.DELIVERY_PRICE: [SenderState.DELIVERY_DESCRIPTION],
    SenderState.DELIVERY_DESCRIPTION: [SenderState.DELIVERY_CONFIRM],

    # Confirmation -> Menu
    SenderState.DELIVERY_CONFIRM: [SenderState.MENU],
    SenderState.VIEW_DELIVERIES: [SenderState.MENU],
}

COURIER_TRANSITIONS = {
    # Registration flow (KYC) [שלב 2]
    CourierState.INITIAL: [CourierState.REGISTER_COLLECT_NAME],
    CourierState.NEW: [CourierState.REGISTER_COLLECT_NAME],
    CourierState.REGISTER_COLLECT_NAME: [CourierState.REGISTER_COLLECT_DOCUMENT],
    CourierState.REGISTER_COLLECT_DOCUMENT: [CourierState.REGISTER_COLLECT_SELFIE],
    CourierState.REGISTER_COLLECT_SELFIE: [CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY],
    CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY: [CourierState.REGISTER_COLLECT_VEHICLE_PHOTO],
    CourierState.REGISTER_COLLECT_VEHICLE_PHOTO: [CourierState.REGISTER_TERMS],
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


# ============================================================================
# שלב 3 - תפריט סדרן היברידי (Dispatcher) [3.2]
# ============================================================================


class DispatcherState(str, Enum):
    """
    מצבי שיחה לסדרן (תפריט היברידי).

    סדרן הוא נהג מאושר עם הרשאות ניהול ברמת תחנה.
    הוא רואה את כל תפריט הנהג + תפריט סדרן ייעודי.
    """

    # תפריט סדרן ראשי
    MENU = "DISPATCHER.MENU"

    # הוספת משלוח - טופס הזנת פרטים
    ADD_SHIPMENT_PICKUP_CITY = "DISPATCHER.ADD_SHIPMENT.PICKUP_CITY"
    ADD_SHIPMENT_PICKUP_STREET = "DISPATCHER.ADD_SHIPMENT.PICKUP_STREET"
    ADD_SHIPMENT_PICKUP_NUMBER = "DISPATCHER.ADD_SHIPMENT.PICKUP_NUMBER"
    ADD_SHIPMENT_DROPOFF_CITY = "DISPATCHER.ADD_SHIPMENT.DROPOFF_CITY"
    ADD_SHIPMENT_DROPOFF_STREET = "DISPATCHER.ADD_SHIPMENT.DROPOFF_STREET"
    ADD_SHIPMENT_DROPOFF_NUMBER = "DISPATCHER.ADD_SHIPMENT.DROPOFF_NUMBER"
    ADD_SHIPMENT_DESCRIPTION = "DISPATCHER.ADD_SHIPMENT.DESCRIPTION"
    ADD_SHIPMENT_FEE = "DISPATCHER.ADD_SHIPMENT.FEE"
    ADD_SHIPMENT_CONFIRM = "DISPATCHER.ADD_SHIPMENT.CONFIRM"

    # צפייה במשלוחים פעילים של התחנה
    VIEW_ACTIVE_SHIPMENTS = "DISPATCHER.VIEW_ACTIVE_SHIPMENTS"

    # היסטוריית משלוחים של התחנה
    VIEW_SHIPMENT_HISTORY = "DISPATCHER.VIEW_SHIPMENT_HISTORY"

    # הוספת חיוב ידני
    MANUAL_CHARGE_DRIVER_NAME = "DISPATCHER.MANUAL_CHARGE.DRIVER_NAME"
    MANUAL_CHARGE_AMOUNT = "DISPATCHER.MANUAL_CHARGE.AMOUNT"
    MANUAL_CHARGE_DESCRIPTION = "DISPATCHER.MANUAL_CHARGE.DESCRIPTION"
    MANUAL_CHARGE_CONFIRM = "DISPATCHER.MANUAL_CHARGE.CONFIRM"


DISPATCHER_TRANSITIONS = {
    # תפריט סדרן ראשי
    DispatcherState.MENU: [
        DispatcherState.ADD_SHIPMENT_PICKUP_CITY,
        DispatcherState.VIEW_ACTIVE_SHIPMENTS,
        DispatcherState.VIEW_SHIPMENT_HISTORY,
        DispatcherState.MANUAL_CHARGE_DRIVER_NAME,
    ],

    # זרימת הוספת משלוח
    DispatcherState.ADD_SHIPMENT_PICKUP_CITY: [DispatcherState.ADD_SHIPMENT_PICKUP_STREET],
    DispatcherState.ADD_SHIPMENT_PICKUP_STREET: [DispatcherState.ADD_SHIPMENT_PICKUP_NUMBER],
    DispatcherState.ADD_SHIPMENT_PICKUP_NUMBER: [DispatcherState.ADD_SHIPMENT_DROPOFF_CITY],
    DispatcherState.ADD_SHIPMENT_DROPOFF_CITY: [DispatcherState.ADD_SHIPMENT_DROPOFF_STREET],
    DispatcherState.ADD_SHIPMENT_DROPOFF_STREET: [DispatcherState.ADD_SHIPMENT_DROPOFF_NUMBER],
    DispatcherState.ADD_SHIPMENT_DROPOFF_NUMBER: [DispatcherState.ADD_SHIPMENT_DESCRIPTION],
    DispatcherState.ADD_SHIPMENT_DESCRIPTION: [DispatcherState.ADD_SHIPMENT_FEE],
    DispatcherState.ADD_SHIPMENT_FEE: [DispatcherState.ADD_SHIPMENT_CONFIRM],
    DispatcherState.ADD_SHIPMENT_CONFIRM: [DispatcherState.MENU],

    # צפייה במשלוחים
    DispatcherState.VIEW_ACTIVE_SHIPMENTS: [DispatcherState.MENU],
    DispatcherState.VIEW_SHIPMENT_HISTORY: [DispatcherState.MENU],

    # זרימת חיוב ידני (כולל חזרה לתפריט מכל שלב)
    DispatcherState.MANUAL_CHARGE_DRIVER_NAME: [DispatcherState.MANUAL_CHARGE_AMOUNT, DispatcherState.MENU],
    DispatcherState.MANUAL_CHARGE_AMOUNT: [DispatcherState.MANUAL_CHARGE_DESCRIPTION, DispatcherState.MENU],
    DispatcherState.MANUAL_CHARGE_DESCRIPTION: [DispatcherState.MANUAL_CHARGE_CONFIRM, DispatcherState.MENU],
    DispatcherState.MANUAL_CHARGE_CONFIRM: [DispatcherState.MENU],
}


# ============================================================================
# שלב 3 - פאנל ניהול תחנה (Station Owner) [3.3]
# ============================================================================


class StationOwnerState(str, Enum):
    """
    מצבי שיחה לבעל תחנה.

    בעל תחנה מנהל סדרנים, ארנק תחנה, דוחות גבייה ורשימה שחורה.
    """

    # תפריט ראשי
    MENU = "STATION.MENU"

    # ניהול בעלים
    MANAGE_OWNERS = "STATION.MANAGE_OWNERS"
    ADD_OWNER_PHONE = "STATION.ADD_OWNER.PHONE"
    REMOVE_OWNER_SELECT = "STATION.REMOVE_OWNER.SELECT"
    CONFIRM_REMOVE_OWNER = "STATION.CONFIRM_REMOVE_OWNER"

    # ניהול סדרנים
    MANAGE_DISPATCHERS = "STATION.MANAGE_DISPATCHERS"
    ADD_DISPATCHER_PHONE = "STATION.ADD_DISPATCHER.PHONE"
    REMOVE_DISPATCHER_SELECT = "STATION.REMOVE_DISPATCHER.SELECT"

    # ארנק תחנה
    VIEW_WALLET = "STATION.VIEW_WALLET"

    # דוח גבייה
    COLLECTION_REPORT = "STATION.COLLECTION_REPORT"

    # רשימה שחורה
    VIEW_BLACKLIST = "STATION.VIEW_BLACKLIST"
    ADD_BLACKLIST_PHONE = "STATION.ADD_BLACKLIST.PHONE"
    ADD_BLACKLIST_REASON = "STATION.ADD_BLACKLIST.REASON"
    REMOVE_BLACKLIST_SELECT = "STATION.REMOVE_BLACKLIST.SELECT"

    # אישור פעולות הרסניות
    CONFIRM_REMOVE_DISPATCHER = "STATION.CONFIRM_REMOVE_DISPATCHER"
    CONFIRM_REMOVE_BLACKLIST = "STATION.CONFIRM_REMOVE_BLACKLIST"

    # עדכון אחוז עמלה
    SET_COMMISSION_RATE = "STATION.SET_COMMISSION_RATE"

    # שלב 4: הגדרות קבוצות תחנה
    GROUP_SETTINGS = "STATION.GROUP_SETTINGS"
    SET_PUBLIC_GROUP = "STATION.SET_PUBLIC_GROUP"
    SET_PRIVATE_GROUP = "STATION.SET_PRIVATE_GROUP"


STATION_OWNER_TRANSITIONS = {
    # תפריט ראשי
    StationOwnerState.MENU: [
        StationOwnerState.MANAGE_OWNERS,
        StationOwnerState.MANAGE_DISPATCHERS,
        StationOwnerState.VIEW_WALLET,
        StationOwnerState.COLLECTION_REPORT,
        StationOwnerState.VIEW_BLACKLIST,
        StationOwnerState.GROUP_SETTINGS,
    ],

    # ניהול בעלים
    StationOwnerState.MANAGE_OWNERS: [
        StationOwnerState.ADD_OWNER_PHONE,
        StationOwnerState.REMOVE_OWNER_SELECT,
        StationOwnerState.MENU,
    ],
    StationOwnerState.ADD_OWNER_PHONE: [
        StationOwnerState.MANAGE_OWNERS,
        StationOwnerState.MENU,
    ],
    StationOwnerState.REMOVE_OWNER_SELECT: [
        StationOwnerState.CONFIRM_REMOVE_OWNER,
        StationOwnerState.MANAGE_OWNERS,
        StationOwnerState.MENU,
    ],
    StationOwnerState.CONFIRM_REMOVE_OWNER: [
        StationOwnerState.MANAGE_OWNERS,
        StationOwnerState.REMOVE_OWNER_SELECT,
        StationOwnerState.MENU,
    ],

    # ניהול סדרנים
    StationOwnerState.MANAGE_DISPATCHERS: [
        StationOwnerState.ADD_DISPATCHER_PHONE,
        StationOwnerState.REMOVE_DISPATCHER_SELECT,
        StationOwnerState.MENU,
    ],
    StationOwnerState.ADD_DISPATCHER_PHONE: [
        StationOwnerState.MANAGE_DISPATCHERS,
        StationOwnerState.MENU,
    ],
    StationOwnerState.REMOVE_DISPATCHER_SELECT: [
        StationOwnerState.CONFIRM_REMOVE_DISPATCHER,
        StationOwnerState.MANAGE_DISPATCHERS,
        StationOwnerState.MENU,
    ],
    StationOwnerState.CONFIRM_REMOVE_DISPATCHER: [
        StationOwnerState.MANAGE_DISPATCHERS,
        StationOwnerState.REMOVE_DISPATCHER_SELECT,
        StationOwnerState.MENU,
    ],

    # ארנק תחנה
    StationOwnerState.VIEW_WALLET: [
        StationOwnerState.SET_COMMISSION_RATE,
        StationOwnerState.MENU,
    ],
    StationOwnerState.SET_COMMISSION_RATE: [
        StationOwnerState.VIEW_WALLET,
        StationOwnerState.MENU,
    ],

    # דוח גבייה
    StationOwnerState.COLLECTION_REPORT: [StationOwnerState.MENU],

    # רשימה שחורה
    StationOwnerState.VIEW_BLACKLIST: [
        StationOwnerState.ADD_BLACKLIST_PHONE,
        StationOwnerState.REMOVE_BLACKLIST_SELECT,
        StationOwnerState.MENU,
    ],
    StationOwnerState.ADD_BLACKLIST_PHONE: [
        StationOwnerState.ADD_BLACKLIST_REASON,
        StationOwnerState.VIEW_BLACKLIST,
    ],
    StationOwnerState.ADD_BLACKLIST_REASON: [
        StationOwnerState.VIEW_BLACKLIST,
        StationOwnerState.MENU,
    ],
    StationOwnerState.REMOVE_BLACKLIST_SELECT: [
        StationOwnerState.CONFIRM_REMOVE_BLACKLIST,
        StationOwnerState.VIEW_BLACKLIST,
        StationOwnerState.MENU,
    ],
    StationOwnerState.CONFIRM_REMOVE_BLACKLIST: [
        StationOwnerState.VIEW_BLACKLIST,
        StationOwnerState.REMOVE_BLACKLIST_SELECT,
        StationOwnerState.MENU,
    ],

    # שלב 4: הגדרות קבוצות
    StationOwnerState.GROUP_SETTINGS: [
        StationOwnerState.SET_PUBLIC_GROUP,
        StationOwnerState.SET_PRIVATE_GROUP,
        StationOwnerState.MENU,
    ],
    StationOwnerState.SET_PUBLIC_GROUP: [
        StationOwnerState.GROUP_SETTINGS,
        StationOwnerState.MENU,
    ],
    StationOwnerState.SET_PRIVATE_GROUP: [
        StationOwnerState.GROUP_SETTINGS,
        StationOwnerState.MENU,
    ],
}
