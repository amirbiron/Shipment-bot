// שאלות חידון מורחבות - Shipment Bot
// שאלות נוספות לשדרוג בוט החידון על הריפו

const expandedQuestions = [

  // ========================================
  // קטגוריה: validation - ולידציה ואבטחה
  // ========================================

  {
    id: 'val_1',
    category: 'validation',
    type: 'multiple',
    question: 'כיצד מוצג מספר טלפון בלוגים לאחר מיסוך עם `PhoneNumberValidator.mask()`?',
    options: [
      '+972*****4567',
      '+97250123****',
      '050-***-4567',
      '****1234567'
    ],
    correct: 1,
    explanation: 'הפונקציה mask() מסתירה את 4 הספרות האחרונות ומחליפה אותן בכוכביות. לדוגמה: +97250123****'
  },
  {
    id: 'val_2',
    category: 'validation',
    type: 'multiple',
    question: 'מה עושה `TextSanitizer.check_for_injection()`?',
    options: [
      'מנקה את הטקסט מתווים מסוכנים ומחזירה טקסט נקי',
      'מחזירה tuple של (is_safe, pattern) - האם הקלט בטוח ואיזה דפוס זוהה',
      'זורקת exception אם מזוהה ניסיון הזרקה',
      'שולחת התרעה לאדמין על ניסיון הזרקה'
    ],
    correct: 1,
    explanation: 'הפונקציה מחזירה tuple: (bool, Optional[str]) - האם הקלט בטוח, ואם לא - איזה דפוס מסוכן זוהה (SQL injection, XSS וכו\').'
  },
  {
    id: 'val_3',
    category: 'validation',
    type: 'multiple',
    question: 'מה האורך המקסימלי שמאפשר `AddressValidator`?',
    options: [
      '100 תווים',
      '150 תווים',
      '200 תווים',
      '500 תווים'
    ],
    correct: 2,
    explanation: 'AddressValidator מגביל כתובות ל-200 תווים מקסימום ו-5 תווים מינימום.'
  },
  {
    id: 'val_4',
    category: 'validation',
    type: 'multiple',
    question: 'איזה מהדפוסים הבאים מזוהה כ-SQL Injection ע"י TextSanitizer?',
    options: [
      'SELECT name FROM users',
      'OR 1=1',
      'שני הנ"ל',
      'אף אחד מהנ"ל'
    ],
    correct: 2,
    explanation: 'TextSanitizer מזהה דפוסי SQL כמו OR 1=1, UNION SELECT, ;DROP, -- ועוד. גם SELECT וגם OR 1=1 הם דפוסים מסוכנים.'
  },
  {
    id: 'val_5',
    category: 'validation',
    type: 'multiple',
    question: 'מה עושה `AddressValidator.normalize()` עם הקיצור `רח\'`?',
    options: [
      'מוחק אותו',
      'ממיר אותו ל-"רחוב"',
      'משאיר אותו כמו שהוא',
      'מחליף אותו ב-"כתובת"'
    ],
    correct: 1,
    explanation: 'AddressValidator.normalize() ממיר קיצורים נפוצים למילים מלאות: רח\' → רחוב, ת.ד. → תא דואר.'
  },
  {
    id: 'val_6',
    category: 'validation',
    type: 'truefalse',
    question: 'AmountValidator מאפשר עד 3 ספרות אחרי הנקודה העשרונית.',
    correct: false,
    explanation: 'AmountValidator מאפשר מקסימום 2 ספרות אחרי הנקודה (מתאים למטבע). הטווח הוא 0.0 עד 100,000.0.'
  },
  {
    id: 'val_7',
    category: 'validation',
    type: 'truefalse',
    question: 'TextSanitizer.sanitize() מבצע גם HTML escaping לטקסט.',
    correct: false,
    explanation: 'sanitize() מבצע ניקוי בסיסי (trim, הסרת null bytes, כיווץ רווחים). ל-HTML escaping יש פונקציה נפרדת: sanitize_for_html().'
  },

  // ========================================
  // קטגוריה: models - מודלים ובסיס נתונים
  // ========================================

  {
    id: 'models_1',
    category: 'models',
    type: 'multiple',
    question: 'למה מודל User משתמש ב-BigInteger כ-Primary Key ולא ב-Integer רגיל?',
    options: [
      'כי זה מהיר יותר בשאילתות',
      'כי מזהי משתמשים של Telegram יכולים לחרוג מטווח int32',
      'כי PostgreSQL דורש BigInteger',
      'כי זה מאפשר UUID'
    ],
    correct: 1,
    explanation: 'מזהי משתמשים של Telegram (user IDs) יכולים לחרוג מטווח int32 (2^31), ולכן נדרש BigInteger כדי לתמוך בהם.'
  },
  {
    id: 'models_2',
    category: 'models',
    type: 'multiple',
    question: 'מה מונע חיוב כפול של שליח על אותה משלוח בטבלת wallet_ledger?',
    options: [
      'בדיקת if בקוד לפני הוספת רשומה',
      'אילוץ UNIQUE על (courier_id, delivery_id, entry_type)',
      'טריגר בבסיס הנתונים',
      'נעילת שורה עם FOR UPDATE בלבד'
    ],
    correct: 1,
    explanation: 'האילוץ UNIQUE על (courier_id, delivery_id, entry_type) מבטיח שלא יכול להיווצר חיוב כפול מאותו סוג על אותה משלוח.'
  },
  {
    id: 'models_3',
    category: 'models',
    type: 'multiple',
    question: 'מהם הסטטוסים האפשריים של משלוח (Delivery)?',
    options: [
      'NEW, ASSIGNED, DELIVERED, CLOSED',
      'OPEN, CAPTURED, IN_PROGRESS, DELIVERED, CANCELLED',
      'PENDING, ACTIVE, COMPLETED, FAILED',
      'CREATED, ACCEPTED, PICKED_UP, DROPPED_OFF'
    ],
    correct: 1,
    explanation: 'מחזור חיי המשלוח: OPEN (חדש) → CAPTURED (שליח תפס) → IN_PROGRESS (בדרך) → DELIVERED (הגיע) או CANCELLED (בוטל).'
  },
  {
    id: 'models_4',
    category: 'models',
    type: 'multiple',
    question: 'מה התפקיד של שדה `token` במודל Delivery?',
    options: [
      'אימות JWT עבור ה-API',
      'טוקן בטוח לסמארט לינקים - מונע ניחוש ID סדרתי',
      'מפתח הצפנה להודעות',
      'מזהה סשן של המשתמש'
    ],
    correct: 1,
    explanation: 'שדה token נוצר באמצעות secrets.token_urlsafe(16) ומאפשר לשליחים לתפוס משלוח דרך לינק בטוח, במקום לחשוף את ה-ID הסדרתי.'
  },
  {
    id: 'models_5',
    category: 'models',
    type: 'multiple',
    question: 'מהו ברירת המחדל של credit_limit בארנק שליח?',
    options: [
      '0 - אין אשראי',
      '-100 ₪',
      '-500 ₪',
      '-1000 ₪'
    ],
    correct: 2,
    explanation: 'ברירת המחדל של credit_limit היא -500.0, כלומר השליח יכול להגיע ליתרה שלילית של עד 500₪ לפני שנחסם.'
  },
  {
    id: 'models_6',
    category: 'models',
    type: 'multiple',
    question: 'מה האילוץ על טבלת conversation_sessions שמבטיח שיחה אחת לכל משתמש בכל פלטפורמה?',
    options: [
      'Primary Key על user_id',
      'UNIQUE על (user_id, platform)',
      'Index על platform',
      'Foreign Key על user_id'
    ],
    correct: 1,
    explanation: 'האילוץ UNIQUE על (user_id, platform) מבטיח שלכל משתמש יש רשומת שיחה אחת בלבד לכל פלטפורמה (WhatsApp או Telegram).'
  },
  {
    id: 'models_7',
    category: 'models',
    type: 'truefalse',
    question: 'מודל StationBlacklist מאפשר לחסום שליח ביותר מתחנה אחת.',
    correct: true,
    explanation: 'האילוץ UNIQUE הוא על (station_id, courier_id), כלומר כל שילוב של תחנה+שליח הוא ייחודי, אבל אותו שליח יכול להיחסם בתחנות שונות.'
  },
  {
    id: 'models_8',
    category: 'models',
    type: 'truefalse',
    question: 'שדה balance_after ב-WalletLedger מחושב בזמן שליפה מבסיס הנתונים.',
    correct: false,
    explanation: 'balance_after נשמר כערך קבוע ברשומת הלדג\'ר בזמן הכתיבה. זה מאפשר מעקב היסטורי מדויק ללא צורך בחישוב מחדש.'
  },

  // ========================================
  // קטגוריה: roles - תפקידים וזרימות
  // ========================================

  {
    id: 'roles_1',
    category: 'roles',
    type: 'multiple',
    question: 'מהם ארבעת התפקידים (UserRole) במערכת?',
    options: [
      'USER, DRIVER, MANAGER, ADMIN',
      'SENDER, COURIER, ADMIN, STATION_OWNER',
      'CLIENT, COURIER, DISPATCHER, ADMIN',
      'SENDER, DRIVER, STATION_OWNER, SUPER_ADMIN'
    ],
    correct: 1,
    explanation: 'ארבעת התפקידים הם: SENDER (שולח), COURIER (שליח), ADMIN (מנהל), STATION_OWNER (בעל תחנה).'
  },
  {
    id: 'roles_2',
    category: 'roles',
    type: 'multiple',
    question: 'מהם הסטטוסים האפשריים לאישור שליח (approval_status)?',
    options: [
      'PENDING, APPROVED, DENIED',
      'NEW, ACTIVE, INACTIVE',
      'PENDING, APPROVED, REJECTED, BLOCKED',
      'WAITING, CONFIRMED, CANCELLED'
    ],
    correct: 2,
    explanation: 'שליח עובר: PENDING (ממתין לאישור) → APPROVED (מאושר) או REJECTED (נדחה). שליח מאושר יכול להיחסם (BLOCKED).'
  },
  {
    id: 'roles_3',
    category: 'roles',
    type: 'multiple',
    question: 'מה ההבדל בין Courier ל-Dispatcher?',
    options: [
      'Dispatcher הוא שליח עם הרשאות אדמין',
      'Dispatcher הוא שליח שמשויך לתחנה ורואה תפריט משולב (שליח + תחנה)',
      'Dispatcher הוא מנהל שיכול גם לשלוח משלוחים',
      'אין הבדל - זה אותו תפקיד'
    ],
    correct: 1,
    explanation: 'Dispatcher הוא שליח שקודם לתפקיד דרך רשומת StationDispatcher. הוא רואה תפריט משולב של שליח רגיל + ניהול תחנה.'
  },
  {
    id: 'roles_4',
    category: 'roles',
    type: 'truefalse',
    question: 'כשמוסיפים תפקיד חדש למערכת, חובה לעדכן את הפונקציה _route_to_role_menu().',
    correct: true,
    explanation: 'לפי הכללים בפרויקט, כל ניתוב איפוס (שורש, #, /start) עובר דרך _route_to_role_menu(), וחובה לעדכן אותה כשמוסיפים תפקיד חדש.'
  },
  {
    id: 'roles_5',
    category: 'roles',
    type: 'truefalse',
    question: 'בניתוב לפי תפקיד, מותר להשתמש ב-else גנרי במקום לטפל בכל תפקיד בנפרד.',
    correct: false,
    explanation: 'אסור! כל if role == חייב לטפל בכל UserRole במפורש. else גנרי עלול לתפוס תפקידים לא צפויים ולגרום לבאגים.'
  },

  // ========================================
  // קטגוריה: webhooks - טיפול בוובהוקים
  // ========================================

  {
    id: 'wh_1',
    category: 'webhooks',
    type: 'multiple',
    question: 'כמה זמן צריך לעבור לפני שהודעת webhook ב-processing נחשבת "stale" ומותר לעבד אותה מחדש?',
    options: [
      '30 שניות',
      '60 שניות',
      '120 שניות',
      '300 שניות'
    ],
    correct: 2,
    explanation: 'אם הודעה נמצאת בסטטוס processing יותר מ-120 שניות (2 דקות), היא נחשבת stale ומותר לעבד אותה מחדש.'
  },
  {
    id: 'wh_2',
    category: 'webhooks',
    type: 'multiple',
    question: 'בטלגרם, כיצד מזהים את המשתמש שלחץ על כפתור בקבוצה?',
    options: [
      'לפי chat.id - מזהה הקבוצה',
      'לפי message.id - מזהה ההודעה',
      'לפי from_user.id - מי שלחץ',
      'לפי callback_query.data - תוכן הכפתור'
    ],
    correct: 2,
    explanation: 'תמיד לזהות לפי from_user.id (מי לחץ), לעולם לא לפי chat.id (איפה ההודעה). זה קריטי בקבוצות שבהן כמה משתמשים פעילים.'
  },
  {
    id: 'wh_3',
    category: 'webhooks',
    type: 'multiple',
    question: 'כיצד מתבצעת המרה מ-HTML לפורמט WhatsApp בהודעות?',
    options: [
      '<b> → **bold**, <i> → *italic*',
      '<b> → *bold*, <i> → _italic_, <s> → ~strikethrough~',
      '<strong> → [bold], <em> → [italic]',
      'לא מתבצעת המרה - WhatsApp תומך ב-HTML'
    ],
    correct: 1,
    explanation: 'המערכת ממירה תגיות HTML לפורמט WhatsApp: <b> → *bold*, <i> → _italic_, <s> → ~strikethrough~, <code> → `code`.'
  },
  {
    id: 'wh_4',
    category: 'webhooks',
    type: 'multiple',
    question: 'אילו מספרים מסוננים לפני שליחת הודעה אישית ב-WhatsApp?',
    options: [
      'מספרים שמתחילים ב-+972 בלבד',
      'מספרים עם tg: (placeholder של טלגרם) ו-@g.us (מזהה קבוצה)',
      'מספרים בינלאומיים שלא מתחילים ב-05',
      'כל המספרים שלא אומתו במערכת'
    ],
    correct: 1,
    explanation: 'לפני שליחת הודעה אישית, המערכת מסננת tg: (placeholder של משתמשי טלגרם) ו-@g.us (מזהי קבוצות WhatsApp).'
  },
  {
    id: 'wh_5',
    category: 'webhooks',
    type: 'truefalse',
    question: 'מנגנון ה-idempotency בוובהוקים משתמש בטבלת webhook_events עם message_id כ-Primary Key.',
    correct: true,
    explanation: 'בדיוק. כל הודעה נכנסת נרשמת בטבלה עם message_id כמפתח ראשי, מה שמונע עיבוד כפול של אותה הודעה.'
  },
  {
    id: 'wh_6',
    category: 'webhooks',
    type: 'truefalse',
    question: 'כפתורים (inline keyboards) עובדים בקבוצות WhatsApp כמו בצ\'אטים פרטיים.',
    correct: false,
    explanation: 'כפתורים לא עובדים בקבוצות. בכל fallback לקבוצה יש להגדיר keyboard=None ולספק הנחיות טקסטואליות במקום.'
  },

  // ========================================
  // קטגוריה: celery - משימות רקע ותורים
  // ========================================

  {
    id: 'celery_1',
    category: 'celery',
    type: 'multiple',
    question: 'כל כמה זמן Celery Beat מריץ את המשימה process_outbox_messages?',
    options: [
      'כל 5 שניות',
      'כל 10 שניות',
      'כל 30 שניות',
      'כל דקה'
    ],
    correct: 1,
    explanation: 'Celery Beat מתזמן את process_outbox_messages כל 10 שניות לעיבוד הודעות ממתינות בטבלת ה-outbox.'
  },
  {
    id: 'celery_2',
    category: 'celery',
    type: 'multiple',
    question: 'מה הנוסחה לחישוב backoff בין ניסיונות חוזרים בתור ה-outbox?',
    options: [
      'base_seconds + retry_count',
      'base_seconds * retry_count',
      'base_seconds * (2 ** retry_count) עם תקרה',
      'base_seconds * (3 ** retry_count)'
    ],
    correct: 2,
    explanation: 'הנוסחה היא exponential backoff: base_seconds * (2 ** retry_count), עם תקרה של max_backoff_seconds (ברירת מחדל: 3600 שניות = שעה).'
  },
  {
    id: 'celery_3',
    category: 'celery',
    type: 'multiple',
    question: 'למה prefetch_multiplier מוגדר ל-1 ב-Celery worker?',
    options: [
      'כדי לחסוך זיכרון',
      'כדי שכל worker יעבד משימה אחת בכל פעם ולא "יחזיק" משימות',
      'בגלל מגבלה של Redis',
      'כדי למנוע deadlocks'
    ],
    correct: 1,
    explanation: 'prefetch_multiplier=1 מבטיח שה-worker שולף משימה אחת בכל פעם מהתור, ולא "מחביא" משימות שworkers אחרים יכולים לעבד.'
  },
  {
    id: 'celery_4',
    category: 'celery',
    type: 'multiple',
    question: 'מה קורה כש-worker של Celery מת באמצע עיבוד משימה (עם ההגדרות הנוכחיות)?',
    options: [
      'המשימה אבודה לצמיתות',
      'המשימה חוזרת לתור בזכות ack_late=True ו-reject_on_worker_lost=True',
      'המשימה מועברת ל-dead letter queue',
      'Redis שומר אותה אוטומטית'
    ],
    correct: 1,
    explanation: 'ack_late=True אומר שהמשימה מאושרת רק אחרי ביצוע (לא לפני). reject_on_worker_lost=True מחזיר אותה לתור אם ה-worker נפל.'
  },
  {
    id: 'celery_5',
    category: 'celery',
    type: 'multiple',
    question: 'אילו קודי HTTP נחשבים transient errors בשליחה ל-WhatsApp Gateway?',
    options: [
      '400, 401, 403, 404',
      '500, 501, 502, 503',
      '502, 503, 504, 429',
      '408, 500, 502, 503'
    ],
    correct: 2,
    explanation: 'קודים 502 (Bad Gateway), 503 (Service Unavailable), 504 (Gateway Timeout) ו-429 (Too Many Requests) נחשבים שגיאות זמניות שמצדיקות ניסיון חוזר.'
  },
  {
    id: 'celery_6',
    category: 'celery',
    type: 'truefalse',
    question: 'הגבול הזמני (time limit) למשימת Celery במערכת הוא 10 דקות.',
    correct: false,
    explanation: 'ה-time limit מוגדר ל-5 דקות (300 שניות). אם משימה חורגת מזמן זה, היא נהרגת.'
  },
  {
    id: 'celery_7',
    category: 'celery',
    type: 'truefalse',
    question: 'אזור הזמן של Celery Beat מוגדר ל-Asia/Jerusalem.',
    correct: true,
    explanation: 'timezone מוגדר ל-Asia/Jerusalem כדי שתזמון משימות (כמו ניקוי יומי) יתבצע לפי שעון ישראל.'
  },

  // ========================================
  // קטגוריה: architecture - ארכיטקטורה ודפוסי עיצוב
  // ========================================

  {
    id: 'arch_1',
    category: 'architecture',
    type: 'multiple',
    question: 'מהם שלושת המצבים של Circuit Breaker?',
    options: [
      'ON, OFF, STANDBY',
      'CLOSED, OPEN, HALF_OPEN',
      'ACTIVE, INACTIVE, RECOVERING',
      'HEALTHY, FAILING, TESTING'
    ],
    correct: 1,
    explanation: 'CLOSED = תקין (בקשות עוברות), OPEN = מנותק (בקשות נחסמות), HALF_OPEN = בודק התאוששות (מאפשר כמה בקשות ניסיון).'
  },
  {
    id: 'arch_2',
    category: 'architecture',
    type: 'multiple',
    question: 'כמה כשלונות נדרשים כדי לפתוח את ה-Circuit Breaker (לעבור מ-CLOSED ל-OPEN)?',
    options: [
      '3',
      '5',
      '10',
      '15'
    ],
    correct: 1,
    explanation: 'failure_threshold מוגדר ל-5. אחרי 5 כשלונות רצופים, ה-Circuit Breaker עובר למצב OPEN וחוסם בקשות.'
  },
  {
    id: 'arch_3',
    category: 'architecture',
    type: 'multiple',
    question: 'מה היתרון המרכזי של דפוס Transactional Outbox?',
    options: [
      'הודעות נשלחות מהר יותר',
      'הודעה נשמרת באותה טרנזקציה עם הפעולה העסקית - מבטיח עקביות ומניעת אובדן',
      'אפשר לשלוח הודעות ללא חיבור לאינטרנט',
      'מפחית את העומס על בסיס הנתונים'
    ],
    correct: 1,
    explanation: 'היתרון המרכזי: ההודעה נשמרת בטבלת outbox באותה טרנזקציה עם הפעולה (למשל יצירת משלוח). זה מבטיח שאם הפעולה הצליחה - ההודעה לא תאבד.'
  },
  {
    id: 'arch_4',
    category: 'architecture',
    type: 'multiple',
    question: 'למה משתמשים ב-`with_for_update()` בפעולות על ארנק?',
    options: [
      'כדי לעדכן את הגרסה של הרשומה',
      'כדי לנעול את השורה (row-level lock) ולמנוע race conditions',
      'כדי לרשום את השינוי בלוג',
      'כדי לשלוח אירוע לתור'
    ],
    correct: 1,
    explanation: 'with_for_update() מפעיל SELECT ... FOR UPDATE שנועל את השורה בבסיס הנתונים. זה מונע מצב שבו שני תהליכים קוראים את אותה יתרה ומעדכנים במקביל.'
  },
  {
    id: 'arch_5',
    category: 'architecture',
    type: 'multiple',
    question: 'מהו Correlation ID ומה תפקידו במערכת?',
    options: [
      'מזהה ייחודי למשתמש שמחליף את ה-user_id',
      'מזהה ייחודי לבקשה שמאפשר מעקב על כל השלבים שלה לאורך המערכת',
      'מזהה של הודעת webhook',
      'מפתח הצפנה לתקשורת בין שירותים'
    ],
    correct: 1,
    explanation: 'Correlation ID הוא מזהה ייחודי שנוצר לכל בקשה ועובר בין כל השכבות (API → Service → Celery). כל לוג כולל אותו למעקב מקצה לקצה.'
  },
  {
    id: 'arch_6',
    category: 'architecture',
    type: 'multiple',
    question: 'איך Correlation ID נשמר בהקשרים אסינכרוניים בלי להעביר אותו כפרמטר?',
    options: [
      'משתנה גלובלי',
      'Thread-local storage',
      'ContextVar מ-contextvars',
      'Environment variable'
    ],
    correct: 2,
    explanation: 'המערכת משתמשת ב-ContextVar מספריית contextvars. ContextVar שורד גבולות async בלי צורך להעביר את הערך במפורש בין פונקציות.'
  },
  {
    id: 'arch_7',
    category: 'architecture',
    type: 'truefalse',
    question: 'למערכת יש Circuit Breakers נפרדים לטלגרם, WhatsApp ו-WhatsApp Admin.',
    correct: true,
    explanation: 'כל שירות חיצוני מקבל Circuit Breaker נפרד, כך שכשל בטלגרם לא ישפיע על שליחת הודעות ב-WhatsApp ולהיפך.'
  },
  {
    id: 'arch_8',
    category: 'architecture',
    type: 'truefalse',
    question: 'כל read-modify-write על ארנק חייב להיות באותה טרנזקציה עם נעילת שורה.',
    correct: true,
    explanation: 'לפי הכללים: כל read-modify-write על ארנק חייב with_for_update(), וכל השדות חייבים להיכתב באותה טרנזקציה. commit ועדכון נפרד אסור.'
  },

  // ========================================
  // קטגוריה: state_machine - מכונת מצבים
  // ========================================

  {
    id: 'sm_1',
    category: 'state_machine',
    type: 'multiple',
    question: 'מה מאוחסן בשדה context_data של conversation_sessions?',
    options: [
      'היסטוריית כל ההודעות של השיחה',
      'נתוני טופס רב-שלבי (כמו כתובת, שם איש קשר וכו\')',
      'מזהה הסשן של הדפדפן',
      'הגדרות שפה של המשתמש'
    ],
    correct: 1,
    explanation: 'context_data (JSON) מאחסן מידע שנאסף לאורך זרימה רב-שלבית. למשל, ביצירת משלוח: עיר איסוף, רחוב, שם איש קשר, וכו\'.'
  },
  {
    id: 'sm_2',
    category: 'state_machine',
    type: 'multiple',
    question: 'מהם ארבעת ה-state classes במכונת המצבים?',
    options: [
      'UserState, AdminState, ManagerState, WorkerState',
      'SenderState, CourierState, DispatcherState, StationOwnerState',
      'ClientState, DriverState, OwnerState, SystemState',
      'InitState, ActiveState, PendingState, CompletedState'
    ],
    correct: 1,
    explanation: 'לכל תפקיד יש מחלקת מצבים משלו: SenderState (שולח), CourierState (שליח), DispatcherState (מנהל משלוחים), StationOwnerState (בעל תחנה).'
  },
  {
    id: 'sm_3',
    category: 'state_machine',
    type: 'multiple',
    question: 'מה בודק ה-guard `_is_in_multi_step_flow` לפני חיפוש מילות מפתח בטקסט?',
    options: [
      'אם המשתמש מחובר למערכת',
      'אם יש חיבור לבסיס הנתונים',
      'אם ה-state מתחיל ב-DISPATCHER., STATION. או states של רישום שליח',
      'אם ההודעה הגיעה מקבוצה'
    ],
    correct: 2,
    explanation: 'ה-guard בודק אם המשתמש באמצע זרימה רב-שלבית (prefixes: DISPATCHER., STATION., ו-states של רישום). זה מונע מצב שכתובת כמו "תחנה מרכזית" תפעיל תפריט שיווקי.'
  },
  {
    id: 'sm_4',
    category: 'state_machine',
    type: 'multiple',
    question: 'מהם שלבי יצירת משלוח מצד השולח (Sender) בסדר הנכון?',
    options: [
      'MENU → PICKUP_CITY → PICKUP_STREET → DROPOFF_CITY → DROPOFF_STREET → CONFIRM',
      'MENU → ADDRESS → DETAILS → PAYMENT → CONFIRM',
      'MENU → PICKUP_CITY → PICKUP_STREET → DROPOFF_CITY → DROPOFF_STREET → URGENCY → DESCRIPTION → CONFIRM',
      'START → PICKUP → DROPOFF → FEE → SEND'
    ],
    correct: 2,
    explanation: 'הזרימה המלאה: MENU → PICKUP_CITY → PICKUP_STREET → DROPOFF_CITY → DROPOFF_STREET → DELIVERY_URGENCY → (אופציונלי: DELIVERY_TIME + DELIVERY_PRICE) → DELIVERY_DESCRIPTION → DELIVERY_CONFIRM.'
  },
  {
    id: 'sm_5',
    category: 'state_machine',
    type: 'truefalse',
    question: 'כשמוסיפים prefix חדש ל-state machine, חובה לעדכן את ה-guard של _is_in_multi_step_flow.',
    correct: true,
    explanation: 'לפי הכללים: כשמוסיפים prefix חדש ל-state machine, חובה לעדכן את ה-guard כדי שהזרימה החדשה לא "תישבר" ע"י חיפוש מילות מפתח.'
  },

  // ========================================
  // קטגוריה: platform - דו-פלטפורמיות
  // ========================================

  {
    id: 'plat_1',
    category: 'platform',
    type: 'multiple',
    question: 'איזה שירות מריץ את ה-WhatsApp Gateway?',
    options: [
      'FastAPI Python service',
      'Node.js microservice (WPPConnect)',
      'Go microservice',
      'Java Spring Boot service'
    ],
    correct: 1,
    explanation: 'ה-WhatsApp Gateway הוא מיקרו-שירות Node.js שמשתמש בספריית WPPConnect. הוא רץ בנפרד מהאפליקציה הראשית.'
  },
  {
    id: 'plat_2',
    category: 'platform',
    type: 'multiple',
    question: 'למה אסור להשתמש ב-asyncio.create_task() לשליחת הודעות רקע?',
    options: [
      'זה איטי מדי',
      'זה לא עובד עם FastAPI',
      'זה בולע exceptions - שגיאות נעלמות בלי התראה',
      'זה יוצר memory leaks'
    ],
    correct: 2,
    explanation: 'asyncio.create_task() בולע exceptions - אם שליחת הודעה נכשלת, השגיאה נעלמת. במקום זה, יש להשתמש ב-background_tasks.add_task() של FastAPI.'
  },
  {
    id: 'plat_3',
    category: 'platform',
    type: 'multiple',
    question: 'מה ה-parse mode שמשמש לשליחת הודעות בטלגרם?',
    options: [
      'Markdown',
      'MarkdownV2',
      'HTML',
      'Plain text'
    ],
    correct: 2,
    explanation: 'המערכת משתמשת ב-HTML parse mode לטלגרם, שתומך בתגיות כמו <b>, <i>, <a> לעיצוב הודעות.'
  },
  {
    id: 'plat_4',
    category: 'platform',
    type: 'multiple',
    question: 'מה ה-fallback הנכון לשם משתמש כשהשם לא זמין?',
    options: [
      'user.name or "אנונימי"',
      'user.full_name or user.name or "לא צוין"',
      'user.display_name or "משתמש"',
      'str(user.id)'
    ],
    correct: 1,
    explanation: 'לפי הכללים: תמיד user.full_name or user.name or "לא צוין" - שרשרת fallback שמבטיחה שתמיד יהיה ערך להצגה.'
  },
  {
    id: 'plat_5',
    category: 'platform',
    type: 'truefalse',
    question: 'לוגיקה חדשה חייבת לעבוד רק בטלגרם ואז אפשר להוסיף תמיכה ב-WhatsApp בהמשך.',
    correct: false,
    explanation: 'לפי הכללים: כל לוגיקה חדשה חייבת לעבוד זהה בשתי הפלטפורמות. אין לשכפל קוד אלא להוציא לשירות משותף.'
  },
  {
    id: 'plat_6',
    category: 'platform',
    type: 'truefalse',
    question: 'הודעות אדמין נשלחות לטלגרם ול-WhatsApp דרך אותו Circuit Breaker.',
    correct: false,
    explanation: 'יש Circuit Breakers נפרדים: אחד לטלגרם, אחד ל-WhatsApp, ואחד ל-WhatsApp Admin. כשל בערוץ אחד לא ישפיע על האחרים.'
  },

  // ========================================
  // קטגוריה: logging - לוגים ומוניטורינג
  // ========================================

  {
    id: 'log_1',
    category: 'logging',
    type: 'multiple',
    question: 'מה הפורמט של לוגים ב-production?',
    options: [
      'Plain text עם timestamp',
      'CSV מופרד בפסיקים',
      'JSON מובנה עם timestamp, level, logger, message, correlation_id ו-extra',
      'Syslog standard format'
    ],
    correct: 2,
    explanation: 'בפרודקשן, JSONFormatter מייצר לוגים מובנים עם כל השדות הנדרשים: timestamp, level, logger, message, correlation_id, ו-extra data.'
  },
  {
    id: 'log_2',
    category: 'logging',
    type: 'multiple',
    question: 'איך מעבירים נתונים נוספים ללוג בצורה הנכונה?',
    options: [
      'logger.info(f"User {user_id} did something")',
      'logger.info("Something happened", extra_data={"user_id": 123})',
      'logger.info("Something happened", user_id=123)',
      'logger.info("Something happened").with_data(user_id=123)'
    ],
    correct: 1,
    explanation: 'הדרך הנכונה היא להשתמש בפרמטר extra_data עם dictionary. זה מופיע בשדה extra בלוג ה-JSON ומאפשר חיפוש וסינון.'
  },
  {
    id: 'log_3',
    category: 'logging',
    type: 'truefalse',
    question: 'מותר להשתמש ב-print() לצורכי דיבוג זמני בפיתוח.',
    correct: false,
    explanation: 'לפי הכללים: אסור להשתמש ב-print() בשום מצב. תמיד להשתמש ב-logger, גם בפיתוח. logger.debug() זמין לצורכי דיבוג.'
  },

  // ========================================
  // קטגוריה: api - נקודות קצה ו-REST
  // ========================================

  {
    id: 'api_1',
    category: 'api',
    type: 'multiple',
    question: 'מהו ה-endpoint לתפיסת משלוח ע"י שליח?',
    options: [
      'PUT /api/deliveries/{id}/assign',
      'POST /api/deliveries/{id}/capture',
      'PATCH /api/deliveries/{id}/status',
      'POST /api/deliveries/{id}/claim'
    ],
    correct: 1,
    explanation: 'POST /api/deliveries/{id}/capture - מבצע תפיסת משלוח עם חיוב אטומי של ארנק השליח.'
  },
  {
    id: 'api_2',
    category: 'api',
    type: 'multiple',
    question: 'מה נדרש לכלול בכל endpoint חדש לפי כללי הפרויקט?',
    options: [
      'רק type hints',
      'רק response_model',
      'תיעוד OpenAPI מלא: response_model, summary, description, responses, tags',
      'רק docstring'
    ],
    correct: 2,
    explanation: 'כל endpoint חייב תיעוד OpenAPI מלא: response_model, summary, description, responses (כולל קודי שגיאה), ו-tags לקיבוץ.'
  },
  {
    id: 'api_3',
    category: 'api',
    type: 'multiple',
    question: 'מה מחזיר ה-endpoint GET /health?',
    options: [
      '{"status": "ok"}',
      '{"status": "healthy"}',
      '{"alive": true}',
      '200 OK ללא body'
    ],
    correct: 1,
    explanation: 'ה-health check endpoint מחזיר {"status": "healthy"} - בדיקת תקינות בסיסית של האפליקציה.'
  },
  {
    id: 'api_4',
    category: 'api',
    type: 'truefalse',
    question: 'מותר ליצור endpoint ללא response_model אם הוא רק מבצע פעולה ולא מחזיר נתונים.',
    correct: false,
    explanation: 'לפי הכללים: כל endpoint חייב תיעוד OpenAPI מלא, כולל response_model. גם endpoints שמבצעים פעולות צריכים להחזיר תגובה מובנית.'
  },

  // ========================================
  // קטגוריה: exceptions - טיפול בשגיאות
  // ========================================

  {
    id: 'exc_1',
    category: 'exceptions',
    type: 'multiple',
    question: 'מה הבעיה עם `raise Exception("Delivery not found")`?',
    options: [
      'Exception לא עובד ב-async',
      'חסר traceback',
      'צריך להשתמש ב-exceptions מותאמים כמו DeliveryNotFoundError עם קוד שגיאה',
      'צריך להשתמש ב-ValueError במקום'
    ],
    correct: 2,
    explanation: 'לפי הכללים: אסור להשתמש ב-exceptions גנריים. יש להשתמש ב-exceptions מותאמים מ-app/core/exceptions.py שכוללים קודי שגיאה ומידע מובנה.'
  },
  {
    id: 'exc_2',
    category: 'exceptions',
    type: 'multiple',
    question: 'איזה exception זורקים כשלשליח אין מספיק אשראי לתפוס משלוח?',
    options: [
      'ValueError("Not enough credit")',
      'InsufficientCreditError',
      'WalletException',
      'PaymentRequiredError'
    ],
    correct: 1,
    explanation: 'InsufficientCreditError הוא exception מותאם שמוגדר ב-app/core/exceptions.py ומתאים בדיוק למצב של חוסר אשראי.'
  },

  // ========================================
  // קטגוריה: config - הגדרות וסביבה
  // ========================================

  {
    id: 'conf_1',
    category: 'config',
    type: 'multiple',
    question: 'מהו גודל הקובץ המקסימלי להעלאה (MAX_FILE_SIZE)?',
    options: [
      '1MB',
      '5MB',
      '10MB',
      '50MB'
    ],
    correct: 2,
    explanation: 'MAX_FILE_SIZE מוגדר ל-10MB (10 * 1024 * 1024 bytes). משמש להגבלת קבצי KYC כמו תמונות תעודת זהות וסלפי.'
  },
  {
    id: 'conf_2',
    category: 'config',
    type: 'multiple',
    question: 'מה קורה בעת הפעלת האפליקציה (startup) לגבי מיגרציות?',
    options: [
      'מיגרציות רצות ידנית דרך CLI',
      'מיגרציות רצות אוטומטית - הוספת enum values ו-columns/indexes',
      'Alembic רץ אוטומטית',
      'בסיס הנתונים נוצר מאפס'
    ],
    correct: 1,
    explanation: 'ב-startup event של FastAPI, אם מדובר ב-PostgreSQL, המערכת מריצה אוטומטית add_enum_values ו-run_all_migrations (הוספת עמודות ואינדקסים).'
  },
  {
    id: 'conf_3',
    category: 'config',
    type: 'multiple',
    question: 'מה ה-backoff המקסימלי (תקרה) לניסיונות חוזרים ב-outbox?',
    options: [
      '5 דקות',
      '30 דקות',
      'שעה (3600 שניות)',
      '24 שעות'
    ],
    correct: 2,
    explanation: 'OUTBOX_MAX_BACKOFF_SECONDS = 3600, כלומר שעה. גם אם הנוסחה האקספוננציאלית נותנת ערך גבוה יותר, לא יחכו יותר משעה בין ניסיונות.'
  },
  {
    id: 'conf_4',
    category: 'config',
    type: 'truefalse',
    question: 'עמלת ברירת המחדל של תחנה (commission_rate) היא 15%.',
    correct: false,
    explanation: 'ברירת המחדל של commission_rate היא 0.10, כלומר 10% ולא 15%.'
  },

  // ========================================
  // קטגוריה: testing - בדיקות
  // ========================================

  {
    id: 'test_1',
    category: 'testing',
    type: 'multiple',
    question: 'איזה בסיס נתונים משמש בבדיקות?',
    options: [
      'PostgreSQL בקונטיינר Docker',
      'SQLite in-memory לבדיקות מהירות',
      'MongoDB mock',
      'H2 Database'
    ],
    correct: 1,
    explanation: 'הבדיקות משתמשות ב-SQLite in-memory לביצועים מהירים. כל בדיקה מקבלת session חדש עם rollback בסוף.'
  },
  {
    id: 'test_2',
    category: 'testing',
    type: 'multiple',
    question: 'איך עושים mock לשירות הטלגרם בבדיקות?',
    options: [
      'מריצים שרת טלגרם מקומי',
      'משתמשים ב-Bot API test environment',
      'עושים patch ל-httpx.AsyncClient.post עם mock שמחזיר 200',
      'משתמשים ב-VCR לתיעוד תגובות'
    ],
    correct: 2,
    explanation: 'ה-fixture mock_telegram עושה patch ל-httpx.AsyncClient.post ומחזיר AsyncMock עם status_code=200 ו-json={"ok": True}.'
  },
  {
    id: 'test_3',
    category: 'testing',
    type: 'truefalse',
    question: 'בבדיקות אסינכרוניות צריך לסמן כל פונקציה עם @pytest.mark.asyncio.',
    correct: false,
    explanation: 'הפרויקט משתמש ב-asyncio_mode=auto (pytest-asyncio 0.23+), כך שכל פונקציית בדיקה async מזוהה אוטומטית ללא צורך בסימון ידני.'
  },

  // ========================================
  // קטגוריה: db_sessions - ניהול סשנים
  // ========================================

  {
    id: 'db_1',
    category: 'db_sessions',
    type: 'multiple',
    question: 'מה ההבדל בין get_db() ל-get_task_session() בניהול חיבורי DB?',
    options: [
      'אין הבדל - שניהם עושים אותו דבר',
      'get_db() לבקשות API (session מתוך pool), get_task_session() ל-Celery (engine חדש לכל משימה)',
      'get_db() סינכרוני ו-get_task_session() אסינכרוני',
      'get_db() ל-reads ו-get_task_session() ל-writes'
    ],
    correct: 1,
    explanation: 'get_db() משתמש ב-AsyncSessionLocal (pool משותף) לבקשות API. get_task_session() יוצר engine חדש לכל משימת Celery ומשחרר אותו בסוף, כי Celery workers רצים בתהליכים נפרדים.'
  },
  {
    id: 'db_2',
    category: 'db_sessions',
    type: 'truefalse',
    question: 'משימות Celery משתמשות באותו connection pool כמו בקשות API.',
    correct: false,
    explanation: 'Celery workers רצים בתהליכים נפרדים ולכן יוצרים engine חדש לכל משימה דרך get_task_session(). שיתוף pool בין תהליכים יגרום לשגיאות "attached to different loop".'
  },

  // ========================================
  // קטגוריה: outbox - דפוס Outbox
  // ========================================

  {
    id: 'outbox_1',
    category: 'outbox',
    type: 'multiple',
    question: 'מהם הסטטוסים האפשריים של הודעה בטבלת outbox_messages?',
    options: [
      'NEW, SENDING, DONE, ERROR',
      'PENDING, PROCESSING, SENT, FAILED',
      'QUEUED, IN_PROGRESS, COMPLETED, REJECTED',
      'DRAFT, SCHEDULED, DELIVERED, BOUNCED'
    ],
    correct: 1,
    explanation: 'הסטטוסים: PENDING (ממתינה), PROCESSING (בעיבוד), SENT (נשלחה בהצלחה), FAILED (נכשלה אחרי כל הניסיונות).'
  },
  {
    id: 'outbox_2',
    category: 'outbox',
    type: 'multiple',
    question: 'כמה ניסיונות חוזרים (retries) מוגדרים לשליחת WhatsApp?',
    options: [
      '1',
      '3',
      '5',
      '10'
    ],
    correct: 1,
    explanation: 'WHATSAPP_MAX_RETRIES מוגדר ל-3 ניסיונות חוזרים לשליחת הודעה דרך WhatsApp Gateway.'
  },
  {
    id: 'outbox_3',
    category: 'outbox',
    type: 'truefalse',
    question: 'הודעות outbox נשלחות סינכרונית כחלק מהטרנזקציה הראשית.',
    correct: false,
    explanation: 'ההודעות רק נשמרות בטבלת outbox באותה טרנזקציה. השליחה בפועל מתבצעת אסינכרונית ע"י Celery worker שמעבד את התור כל 10 שניות.'
  }
];

module.exports = expandedQuestions;
