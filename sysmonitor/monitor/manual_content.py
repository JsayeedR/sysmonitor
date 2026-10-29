"""
Safe, user-facing SysMonitor manual content.

This module intentionally contains no credentials, tokens, private addresses,
recipient details, or other operational secrets.  The same content is reused by
the HTML manual and the downloadable PDF.
"""

MANUAL_INTRO = {
    "title": "SysMonitor Operations Manual",
    "subtitle": (
        "A practical guide to the monitoring, reporting, generator, colocation, "
        "notification, CCTV, profile, and administrative functions available in "
        "SysMonitor."
    ),
    "scope": (
        "This manual explains what each page means, how information moves through "
        "the system, what users should do, and where authorized administrators "
        "manage settings. It deliberately excludes passwords, tokens, private "
        "addresses, personal recipient data, and other secrets."
    ),
}

ROLE_GUIDE = [
    {
        "role": "Viewer",
        "summary": "Read-focused operational visibility.",
        "details": (
            "Can use the operational pages granted to the Viewer role, review "
            "status/history, and use non-administrative tools made available by "
            "Page Access."
        ),
    },
    {
        "role": "Full Viewer (dedicated account)",
        "summary": "Broad operational visibility with server-enforced read-only restrictions.",
        "details": (
            "Full Viewer has broad read-only visibility across approved operational, roster, "
            "report and administrative overview pages, including device statistics and the live refresh dial. "
            "It cannot change records, send reports, start maintenance, download/export files, "
            "or view stored gateway secrets. Pages with unreviewed GET side effects or sensitive "
            "credential exposure remain restricted until individually audited. The account is not a superuser."
        ),
    },
    {
        "role": "User",
        "summary": "Operational work plus shift/report entry.",
        "details": (
            "Can perform normal operational reporting and data-entry functions "
            "such as Shift Report and approved Generator Data workflows, subject "
            "to Page Access."
        ),
    },
    {
        "role": "Admin",
        "summary": "Full application administration.",
        "details": (
            "Can manage users, devices, notifications, page visibility, CCTV "
            "configuration, colocation setpoints, system controls, and other "
            "administrative settings."
        ),
    },
]

STATUS_GLOSSARY = [
    ("NORMAL", "Normal monitored condition; no active power-cycle exception."),
    ("OUTAGE", "A monitored power outage is in progress."),
    ("GENERATOR", "Generator-backed operation is in progress while utility power is unavailable."),
    ("ATS", "Automatic transfer / restoration transition is being observed."),
    ("DEVICE DOWN", "A monitored device or endpoint is currently unreachable."),
    ("ONLINE", "The source is reachable and its data is considered current."),
    ("OFFLINE", "The source is unavailable or its data is too stale to be trusted as current."),
    ("NOTICE", "Operational information that should be read but is not necessarily an alarm."),
    ("ALARM / CRITICAL", "An abnormal condition requiring attention or investigation."),
]

ARCHITECTURE_STEPS = [
    ("1", "Collect", "Monitoring services poll approved devices and data sources."),
    ("2", "Validate", "SysMonitor checks availability, freshness, timestamps, and state."),
    ("3", "Store", "Operational readings, events, cycles, reports, and audit data are stored."),
    ("4", "Present", "Role-controlled pages turn stored state into live cards, tables, and reports."),
    ("5", "Notify", "Configured notification rules can send approved alerts or scheduled updates."),
    ("6", "Mirror", "The remote instance receives the approved mirrored state for continuity."),
]

PAGE_GUIDES = [
    {
        "id": "dashboard",
        "group": "Main",
        "icon": "📊",
        "title": "Dashboard",
        "audience": "Viewer · User · Admin",
        "purpose": "The operational overview page for the current condition of SysMonitor.",
        "how": [
            "Combines monitored device state, power-cycle state, current operational summaries, and recent information into one screen.",
            "Live sections refresh automatically; the page is intended for observation rather than manual data editing.",
            "Status colours and labels should be read together with timestamps so an operator can distinguish a current condition from old history.",
        ],
        "means": [
            "Use the current-state banner first to understand whether the system is normal, in outage, on generator, in ATS transition, or seeing a device problem.",
            "Recent events explain why a state changed. Historical reports provide the longer-term record.",
            "When a card is stale or unavailable, verify the relevant detailed page instead of assuming the last displayed value is current.",
        ],
        "manage": "Operational configuration is managed through the related Administrative pages; the Dashboard itself is primarily read-only.",
    },
    {
        "id": "colocation",
        "group": "Main",
        "icon": "🌡️",
        "title": "Colocation",
        "audience": "Viewer · User · Admin",
        "purpose": "Shows colocation-room environmental data and sensor availability.",
        "how": [
            "Displays temperature, humidity, battery availability, online/offline state, latest SysMonitor poll, and last physical sensor update.",
            "History charts use stored readings. Offline periods are intentionally blank rather than repeating cached sensor values as if they were live.",
            "A source-update timestamp represents the last fresh data received from the physical sensor; Last Poll represents when SysMonitor checked it.",
        ],
        "means": [
            "ONLINE means the physical device is reported online and its source data is fresh enough to trust.",
            "OFFLINE means live environmental values must not be treated as current. Temperature and humidity may therefore show a dash.",
            "Out of battery / unavailable means SysMonitor cannot safely report a current battery value while the device is offline.",
        ],
        "manage": "Authorized admins manage alarm thresholds from Administrative → Colocation Setpoints. Notification recipients are managed through the notification pages.",
    },
    {
        "id": "load-shedding",
        "group": "Main",
        "icon": "⚡",
        "title": "Load Shedding",
        "audience": "Viewer · User · Admin",
        "purpose": "Provides historical outage-cycle reporting and exportable power-event records.",
        "how": [
            "A Remarks column immediately after Type shows saved Outage Cycle Remarks recorded by engineers on the associated cycle.",
            "Builds reports from completed outage-cycle records and their measured durations.",
            "Filters allow operators to narrow the reporting period before reviewing or exporting results.",
            "CSV/PDF exports are generated from the same report data so downloaded records correspond to the selected report period.",
        ],
        "means": [
            "Completed cycles are historical records; an active outage belongs to live monitoring until it closes.",
            "Different cycle classifications separate normal outages from special or audited conditions.",
        ],
        "manage": "Normal users consume this report. Corrections to generator/power-cycle history should use the authorized Generator Manual Cycle workflow instead of editing report output.",
    },
    {
        "id": "shift-report",
        "group": "Main",
        "icon": "📝",
        "title": "Shift Report",
        "audience": "User · Admin",
        "purpose": "Creates auditable morning, evening, and night handover reports with live operational summaries and controlled email delivery.",
        "how": [
            "You may attach optional supporting files while preparing a report; saved attachments accompany the outgoing report email. The required Historical Important Issues workbook is not replaced by these optional files.",
            "MNOC Daily PFE has its own optional attachments for manual and scheduled sending. Do not attach credentials or unnecessarily sensitive documents.",
            "Choose the report date and Morning, Evening, or Night shift. Drafts let the engineer prepare activities, AC shifting selections, remarks, historical issues, and handover details before the shift finishes.",
            "Load Shedding is read from SysMonitor outage cycles for the selected shift window. Outage start/end times are shown in Bangladesh time (BDT) with 12-hour AM/PM formatting; duration is based on the outage interval, not assumed generator running time.",
            "When no outage is recorded for a shift, the report shows 'No load shedding occurred during the shift.' once. Select another date or shift to refresh the on-screen operational preview.",
            "For Night Shift only, Section B (Regular Shift Activities) includes the PREVIOUS calendar day's Generator Log, directly after SMW4 CIRCUIT & BANDWIDTH STATUS. It shows generator starts/ends and Gen-01, Gen-02, and grand totals where genuine generator runtime is recorded.",
            "The Night Shift calendar-day Generator Log uses the same source and day-splitting algorithm as the daily Generator Log email/Telegram message, including audited manual cycles and generator-mode changes. Rows and Gen-01 / Gen-02 totals must match for the same BDT date.",
            "A PDB outage does not by itself establish a generator run. Check recorded cycles and generator-mode history if the source daily Generator Log is wrong; do not invent runtime in the Shift Report.",
            "WET Segment maximum circuit configuration sets Assigned Capacity (sum of maximum circuits multiplied by 100, 10, 2.488, and 0.15552 Gbps). The report's selected traffic-carrying circuit counts determine Used Capacity. Assigned, No Traffic = Assigned minus Used; Unassigned Capacity = 4,650 minus Assigned; each percentage is relative to the fixed 4,650 Gbps total. Circuit rows show carrying / WET-activated, with Wet-Activated and Not Carrying Traffic counts computed from WET maximum counts. A +1 maximum 10G circuit adds 10 Gbps Assigned Capacity; it does not change Used Capacity unless carrying circuits change. The exact dash-separated plain-text SMW4 block is frozen with the BDT submission time on first email sending, and older SENT reports remain unchanged.",
            "An unsent draft refreshes operational snapshots when reopened or saved. A shared manual/scheduled sending handler refreshes them again before the first main email is prepared. These refreshes must not overwrite the engineer's typed remarks, activities, or AC shifting selections.",
            "A successfully SENT report opens the identical read-only report view for both its author and other engineers, including direct old edit links. DRAFT reports remain editable by their owner or authorized Admin.",
            "An already-emailed SENT snapshot remains frozen during retries and when underlying generator cycles/WET configuration are subsequently edited. DRAFT operational facts are refreshed before sending; old SENT reports are not silently rewritten.",
            "Use Historical Reports and MNOC/PFE options only for their intended operational reporting and revision workflows. Check recipient/configuration and actual sending status before claiming delivery succeeded.",
        ],
        "means": [
            "DRAFT = editable work in progress; live outage and sensor records may change before sending. Reopen/save to refresh operational facts.",
            "SENT = preserved report/revision, not an editable working draft. Corrections to source outage data do not retroactively change a successfully sent report.",
            "Previous-day Generator Log is deliberately Night-only; load shedding belongs to the selected report shift, not the full preceding day.",
            "Automated scheduled sending processes only explicitly opted-in eligible drafts after shift end; it is not triggered just by saving a draft.",
        ],
        "manage": "Authorized admins configure handover contacts, email recipients, sending schedule, signatures, and reporting settings in Administrative → Shift Report Configuration.",
    },
    {
        "id": "outage-cycle-remarks",
        "group": "Generator",
        "icon": "🗒️",
        "title": "Outage Cycle Remarks",
        "audience": "User · Admin (Full Viewer: read-only)",
        "purpose": "Records attributed engineer remarks on individual outage cycles without changing the underlying cycle measurements.",
        "how": [
            "Open Generator → Outage Cycle Remarks (after Manual Cycle Entry). The default filter is This Week in Bangladesh local time.",
            "Use Last 10, This Week, Last Week, This Month, Last Month, This Year or All Time; use From and To plus Filter for a custom date interval. Refresh re-queries the selected view.",
            "Find the relevant cycle, review its times and classification, and add one remark for your own account on that cycle. Do not submit duplicate or speculative entries.",
            "Administrators may correct or delete saved remarks. Standard users cannot edit or delete their already submitted remark.",
            "The same remarks appear on Load Shedding after Type and on Generator Cycle Audit; CSV/PDF exports are available only where the user's access permissions permit them.",
        ],
        "means": [
            "Remarks provide attributed contextual notes, not evidence that monitored outage duration or generator runtime has changed.",
            "An automatically detected cycle and an audited/manual cycle are different record types; select the actual event before writing.",
        ],
        "manage": "Use Generator → Outage Cycle Remarks. Admins manage corrections; other engineers add one note per cycle.",
    },
    {
        "id": "duty-roster",
        "group": "Main",
        "icon": "📅",
        "title": "Duty Roster and Shift Dashboard",
        "audience": "Viewer · User · Admin (as permitted)",
        "purpose": "Helps authorized operators view monthly duty assignments and shift coverage alongside operational reporting.",
        "how": [
            "The expanded Live Duty Preview displays more upcoming duty slots. An engineer keeps a consistent display colour, helping distinguish people across adjacent shifts.",
            "Open the Duty Roster or Shift Dashboard entry available to your role to review the appropriate month's assignment information.",
            "Read the roster date and assignment before preparing a handover; duty assignments and actual completed Shift Reports are separate records.",
            "Only authorized users should make roster corrections. Confirm changes through the roster's own approved workflow, not by editing an already-sent Shift Report.",
        ],
        "means": [
            "A roster is a planned or maintained duty assignment; it does not prove an email was sent or a shift report was completed.",
            "Page availability and editable fields depend on the installed roster workflow and account permissions.",
        ],
        "manage": "Use the installed Duty Roster/Shift Dashboard navigation and its role-controlled management functions; contact an administrator if the page is not visible.",
    },
    {
        "id": "generator-shifting",
        "group": "Generator Data",
        "icon": "🔄",
        "title": "Generator Shifting Entry",
        "audience": "User · Admin",
        "purpose": "Records operational generator assignment / shifting information.",
        "how": [
            "Operators enter the generator mode or shift change at the time the operational assignment changes.",
            "The history supports later runtime and operational review.",
        ],
        "means": [
            "Entries should represent the actual operational handover, not a prediction.",
            "Accurate timestamps are important because downstream reporting can depend on the recorded sequence.",
        ],
        "manage": "Use Generator Data → Generator Shifting Entry. Corrections should be made by authorized operators with the audit trail in mind.",
    },
    {
        "id": "generator-fuel-entry",
        "group": "Generator Data",
        "icon": "⛽",
        "title": "Generator Fuel Entry",
        "audience": "User · Admin",
        "purpose": "Records generator fuel loading, reserve, runtime, usage, and related operational information.",
        "how": [
            "Fuel entries preserve the hard-register style operational record in a searchable system.",
            "Calculated values and reports depend on consistent source values and dates.",
        ],
        "means": [
            "Fuel loaded, fuel used, reserve, runtime, and average consumption are different measures and should not be interchanged.",
            "Remarks should explain exceptional conditions rather than duplicate obvious numeric values.",
        ],
        "manage": "Use Generator Data → Generator Fuel Entry. Historical correction should follow the same authorized data-entry controls.",
    },
    {
        "id": "generator-manual-cycle",
        "group": "Generator Data",
        "icon": "🕵️",
        "title": "Generator Manual Cycle Entry",
        "audience": "User · Admin",
        "purpose": "Provides an audited way to add or correct power/generator cycles when automatic monitoring cannot represent the real event.",
        "how": [
            "Generator Cycle Audit displays remarks entered on the separate Outage Cycle Remarks page alongside cycle audit information.",
            "Operators enter the known start/end and relevant classification for an exceptional or historical cycle.",
            "Manual cycles are kept distinguishable from automatically detected cycles.",
        ],
        "means": [
            "Use this page for genuine corrections or backfill, not to overwrite a live automatically detected incident.",
            "The audit trail is important because manual entries alter historical reports.",
        ],
        "manage": "Use Generator Data → Generator Manual Cycle Entry. Admin review may be appropriate for unusual corrections.",
    },
    {
        "id": "generator-fuel-report",
        "group": "Generator Data",
        "icon": "📊",
        "title": "Generator Fuel Report",
        "audience": "Viewer · User · Admin",
        "purpose": "Summarizes historical generator fuel activity for review and reporting.",
        "how": [
            "Uses stored fuel records to present period-based operational summaries.",
            "The report is read-only; corrections belong in the source entry workflow.",
        ],
        "means": [
            "Treat calculated averages as derived values based on the quality of the underlying fuel/runtime entries.",
        ],
        "manage": "Fuel data is maintained from Generator Fuel Entry by authorized users; report consumers should not edit output directly.",
    },
    {
        "id": "generator-runtime",
        "group": "Generator Data",
        "icon": "⏱️",
        "title": "Generator Runtime",
        "audience": "Viewer · User · Admin",
        "purpose": "Shows generator operating-duration history derived from monitored/manual cycle information.",
        "how": [
            "Runtime summaries use the recorded power/generator event timeline.",
            "Filters help isolate a reporting period and compare operational duration.",
        ],
        "means": [
            "Runtime is historical duration, not necessarily fuel consumption; fuel calculations belong to the fuel workflow.",
        ],
        "manage": "If runtime is wrong, verify the underlying cycles and generator-shifting history rather than editing the report presentation.",
    },
    {
        "id": "smw6pac",
        "group": "Others",
        "icon": "❄️",
        "title": "SMW6PAC",
        "audience": "Viewer · Admin",
        "purpose": "Shows the monitored status of the SMW6 PAC / cooling-related source.",
        "how": [
            "Reads the configured PAC source and presents the state without requiring operators to access the source system directly.",
            "Availability depends on the configured integration and network reachability.",
        ],
        "means": [
            "An unavailable reading means the source could not be confirmed; it should not be interpreted as a valid running/stopped state.",
        ],
        "manage": "Integration settings are maintained by authorized administrators outside the normal Viewer workflow.",
    },
    {
        "id": "uptime",
        "group": "Others",
        "icon": "🌐",
        "title": "Uptime Status",
        "audience": "Viewer · User · Admin",
        "purpose": "Consolidates service/endpoint uptime information from the configured monitoring source.",
        "how": [
            "Displays monitored targets and their current state, with access to available monitor history/log information.",
            "It is a visibility layer; the authoritative service owner remains responsible for fixing an external target.",
        ],
        "means": [
            "DOWN means the monitoring source reports the endpoint unavailable. Confirm planned maintenance before escalating.",
        ],
        "manage": "Target definitions are controlled through the connected uptime-monitoring system or authorized SysMonitor configuration.",
    },
    {
        "id": "events",
        "group": "Others",
        "icon": "📋",
        "title": "All Events",
        "audience": "Viewer · User · Admin",
        "purpose": "Provides the chronological operational event history generated by monitoring and approved workflows.",
        "how": [
            "Events are created when monitoring states change or when important automated/manual operations complete.",
            "The page is intended for investigation and chronology; it is not a substitute for the Activity audit log.",
        ],
        "means": [
            "INFO/NOTICE entries are informational. ALARM/CRITICAL entries indicate abnormal conditions.",
            "Read the event timestamp, level, and message together when reconstructing an incident.",
        ],
        "manage": "Event generation is automatic from the related monitoring workflow. Operators should correct the source issue rather than editing historical event text.",
    },
    {
        "id": "cctv",
        "group": "Others",
        "icon": "🎥",
        "title": "NOC CCTV",
        "audience": "Viewer · User · Admin",
        "purpose": "Provides approved live CCTV viewing without storing camera video in the normal SysMonitor database.",
        "how": [
            "SysMonitor acts as a controlled viewing layer for configured camera streams.",
            "Stream availability depends on the NVR/camera and the configured secure streaming path.",
        ],
        "means": [
            "An unavailable stream can indicate camera, NVR, network, tunnel, or media-gateway availability issues.",
        ],
        "manage": "Authorized admins manage camera/NVR definitions from Administrative → CCTV Setup. Credentials are never shown in this manual.",
    },
    {
        "id": "notification-request",
        "group": "Others",
        "icon": "🔔",
        "title": "Notification Request",
        "audience": "Viewer · User · Admin",
        "purpose": "Allows a user to request or manage approved notification preferences without exposing gateway credentials.",
        "how": [
            "Users submit the permitted notification preference/request and authorized administration completes or approves the configuration.",
            "Supported event categories can include operational alerts and colocation updates according to the current configuration.",
        ],
        "means": [
            "A request is not the same as a successful delivery; delivery status is determined by the configured gateway and recipient setup.",
        ],
        "manage": "User-facing requests are made here. Gateway credentials, recipient administration, tests, and message templates belong to Administrative → Notifications.",
    },
    {
        "id": "profile",
        "group": "Others",
        "icon": "👤",
        "title": "My Profile",
        "audience": "Viewer · User · Admin",
        "purpose": "Shows the signed-in user's own profile and account-maintenance options.",
        "how": [
            "Users can review their profile and submit permitted changes.",
            "Password changes use the authenticated account workflow; sensitive password values are never displayed back to the user.",
        ],
        "means": [
            "Some profile changes may require approval depending on the field and configured workflow.",
        ],
        "manage": "Use Others → My Profile for self-service changes. User administration is handled separately by admins.",
    },
    {
        "id": "about",
        "group": "Others",
        "icon": "ℹ️",
        "title": "About Us",
        "audience": "Public / Signed-in users",
        "purpose": "Explains the purpose, architecture, project background, and non-sensitive documentation for SysMonitor.",
        "how": [
            "This page is informational and intentionally excludes credentials and private operational secrets.",
        ],
        "means": [
            "Use the Manual for detailed operating instructions; use About Us for project/background information.",
        ],
        "manage": "Documentation content is maintained as part of SysMonitor code releases.",
    },
    {
        "id": "manual",
        "group": "Others",
        "icon": "📘",
        "title": "Manual",
        "audience": "Viewer · User · Admin",
        "purpose": "The single-page operations reference you are reading now.",
        "how": [
            "The on-screen manual follows the running SysMonitor version and is organized with hyperlinks for quick navigation.",
            "The PDF button creates a downloadable manual using the same maintained guide content.",
        ],
        "means": [
            "The version shown on the manual identifies the SysMonitor revision whose workflow the guide describes.",
        ],
        "manage": "Manual content is updated with meaningful SysMonitor releases so operating guidance can evolve with the application.",
    },
    {
        "id": "devices",
        "group": "Administrative",
        "icon": "🖥️",
        "title": "Devices",
        "audience": "Admin",
        "purpose": "Manages the approved devices that SysMonitor monitors.",
        "how": [
            "Admins add/edit supported device definitions used by monitoring processes.",
            "Changing a device definition can affect live monitoring and should be performed deliberately.",
        ],
        "means": [
            "Active devices participate in the relevant monitoring workflow; inactive/removed definitions should not be assumed to produce live status.",
        ],
        "manage": "Administrative → Devices. Never place passwords or secret tokens in descriptive fields.",
    },
    {
        "id": "users",
        "group": "Administrative",
        "icon": "👥",
        "title": "Users",
        "audience": "Admin",
        "purpose": "Creates and maintains SysMonitor accounts and assigned roles.",
        "how": [
            "The dedicated fullviewer@sysmonitor account and accounts explicitly assigned the Full Viewer role use server-enforced read-only checks. Never grant them staff or superuser rights to broaden visibility.",
            "Admins create accounts, assign the intended role, and manage account state.",
            "Role is the maximum permission boundary; Page Access can only reduce the pages available to that role.",
        ],
        "means": [
            "Disabling an account prevents normal sign-in without deleting the historical audit trail associated with that user.",
        ],
        "manage": "Administrative → Users. Passwords should never be copied into notes, manuals, or screenshots.",
    },
    {
        "id": "page-access",
        "group": "Administrative",
        "icon": "🔐",
        "title": "Page Access",
        "audience": "Admin",
        "purpose": "Lets administrators hide selected pages for individual users without increasing their role permissions.",
        "how": [
            "The user's role is checked first. Page Access can remove a page from that role but cannot grant a page the role does not own.",
            "Superadmin recovery access to Page Access remains protected; Superadmin may hide this page from ordinary Admin accounts.",
        ],
        "means": [
            "A hidden page disappears from navigation and direct access is blocked by middleware.",
        ],
        "manage": "Administrative → Page Access.",
    },
    {
        "id": "cctv-setup",
        "group": "Administrative",
        "icon": "🎥",
        "title": "CCTV Setup",
        "audience": "Admin",
        "purpose": "Maintains NVR and camera definitions used by the NOC CCTV page.",
        "how": [
            "Admins define the approved stream endpoints and camera metadata required by the viewing layer.",
            "Changes should be tested from NOC CCTV after saving.",
        ],
        "means": [
            "Configuration availability does not guarantee the camera is physically online.",
        ],
        "manage": "Administrative → CCTV Setup. Sensitive stream credentials must remain protected and are not documented here.",
    },
    {
        "id": "shift-config",
        "group": "Administrative",
        "icon": "⚙️",
        "title": "Shift Report Configuration",
        "audience": "Admin",
        "purpose": "Controls the administrative settings used by Shift Report delivery and handover workflows.",
        "how": [
            "Maintains approved report settings, handover contacts, scheduling, and related report-delivery configuration.",
            "Changes affect future report preparation/sending and should be verified carefully.",
        ],
        "means": [
            "Configuration changes do not rewrite previously sent historical revisions.",
        ],
        "manage": "Administrative → Shift Report Configuration.",
    },
    {
        "id": "notifications-admin",
        "group": "Administrative",
        "icon": "🔔",
        "title": "Notifications",
        "audience": "Admin",
        "purpose": "Controls notification gateways, recipients, event selections, templates, and delivery tests.",
        "how": [
            "Admins configure supported channels and select who receives approved event types, including colocation data updates and sensor alarms when enabled.",
            "Recipient groups distinguish linked accounts from manually configured contacts; each event/category has its own selection.",
            "Test functions verify delivery without exposing credentials to normal users.",
            "Notification logs help distinguish a generated alert from a successfully delivered message.",
        ],
        "means": [
            "A configured recipient may receive only the event types enabled for that recipient.",
            "Gateway failure and recipient configuration are separate causes of delivery failure.",
        ],
        "manage": "Administrative → Notifications. Gateway secrets must never be copied into documentation, screenshots, or chat.",
    },
    {
        "id": "activity",
        "group": "Administrative",
        "icon": "🕵️",
        "title": "Activity",
        "audience": "Admin",
        "purpose": "Provides the audit trail for user/account/configuration actions.",
        "how": [
            "Records supported security and administration actions with user/time context.",
            "It is separate from All Events: Activity focuses on who changed or performed something; Events focus on operational system history.",
        ],
        "means": [
            "Use Activity when investigating account/configuration changes and All Events when reconstructing monitored operational conditions.",
        ],
        "manage": "Administrative → Activity. Audit records should be preserved rather than edited casually.",
    },
    {
        "id": "system",
        "group": "Administrative",
        "icon": "🛠️",
        "title": "System",
        "audience": "Admin",
        "purpose": "Provides controlled system-health, journal, maintenance, cycle, and authorized service-control functions.",
        "how": [
            "Shows live system information and exposes only fixed, permission-checked administrative actions.",
            "Restart controls are protected and are designed for the MASTER role; the remote mirror must not trigger MASTER service restarts.",
        ],
        "means": [
            "A restart is an operational action, not a troubleshooting substitute. Read the current state and journal information first.",
            "Maintenance mode should be used only for intentional maintenance windows.",
        ],
        "manage": "Administrative → System. Follow approved operational procedures before using restart or manual-cycle controls.",
    },
    {
        "id": "colocation-setpoints",
        "group": "Administrative",
        "icon": "🌡️",
        "title": "Colocation Setpoints",
        "audience": "Admin",
        "purpose": "Defines environmental alarm thresholds and related sensor alarm behaviour.",
        "how": [
            "Admins set acceptable minimum and maximum temperature/humidity boundaries used by the sensor alarm workflow.",
            "Alarm confirmation readings and cooldown are configurable so brief spikes and repeated alerts can be handled consistently.",
            "Threshold alerts are meaningful only when fresh sensor data is available.",
        ],
        "means": [
            "An offline sensor is an availability problem; it must not be treated as a real high/low environmental reading.",
        ],
        "manage": "Administrative → Colocation Setpoints. Use operationally approved thresholds rather than arbitrary test values.",
    },
]

OPERATING_WORKFLOWS = [
    {
        "title": "Power outage investigation",
        "steps": [
            "Read Dashboard current state and timestamp.",
            "Open All Events to identify the sequence of outage/generator/ATS transitions.",
            "Use Load Shedding and Generator Runtime after the cycle closes for historical duration.",
            "Use Generator Manual Cycle only if a genuine correction/backfill is required.",
        ],
    },
    {
        "title": "Colocation sensor offline",
        "steps": [
            "Confirm Colocation shows OFFLINE and live temperature/humidity are blank.",
            "Compare Last Poll with Last Sensor Update to distinguish SysMonitor polling from physical sensor data.",
            "Check battery/power/network availability of the sensor as appropriate.",
            "Do not use cached values as live environmental measurements.",
        ],
    },
    {
        "title": "Notification not received",
        "steps": [
            "Confirm the operational event actually occurred.",
            "Check whether the recipient requested/was configured for that event type.",
            "Admins review notification logs and gateway health/test functions.",
            "Do not expose gateway tokens or personal recipient details while troubleshooting.",
        ],
    },
    {
        "title": "Shift Report drafted before an outage",
        "steps": [
            "Create and save a DRAFT while the shift is under way; do not send it as a completed report prematurely.",
            "If load shedding occurs later, verify the power cycle appears in SysMonitor; reopening or saving the draft refreshes its system data.",
            "Check the selected shift's Load Shedding details and, for Night Shift, the separate previous-day Generator Log.",
            "Before initial delivery, the manual/scheduled send handler refreshes operational facts; after successful email sending, the retained snapshot is protected.",
        ],
    },
    {
        "title": "Report correction",
        "steps": [
            "Correct the source operational record, not the exported PDF/CSV itself.",
            "Regenerate the report after the source data is corrected.",
            "For sent Shift Reports, do not alter the sent snapshot. Correct the underlying records and use the approved historical/revision workflow if a follow-up is needed.",
        ],
    },
]

# Newly deployed operator workflows.
OPERATING_WORKFLOWS.extend([
    {
        "title": "Outage remark and report review",
        "steps": [
            "Open Generator → Outage Cycle Remarks; use This Week or a custom From/To filter to locate the cycle.",
            "Add a factual, attributed remark once; request admin correction instead of adding another account's remark.",
            "Confirm the remark appears in the Load Shedding Remarks column and Generator Cycle Audit.",
        ],
    },
    {
        "title": "Independent NVR critical alert",
        "steps": [
            "If the CCTV NVR is confirmed down for five minutes, an independent critical notification is sent to enabled critical subscribers.",
            "While the NVR remains down, the additional alert repeats at two-minute intervals; it sends a recovery notification after the NVR comes back online following an alerted outage.",
            "This operates independently of the existing both-device-down ten-minute critical threshold. An NVR loss indicates a monitoring and battery-backup risk; do not assume an actual generator failure without checking evidence.",
        ],
    },
    {
        "title": "Supporting files in Shift and MNOC PFE reports",
        "steps": [
            "Prepare the draft and attach relevant optional supporting files only when needed.",
            "Review the saved draft and attachments before sending; Shift Report retains its Historical Important Issues workbook alongside any extra files.",
            "Respect the existing shift-end, reading-date, sent-report protection and scheduled-send rules. Verify final sent status instead of assuming that saving means an email was delivered.",
        ],
    },
])

# Separate admin-only section in HTML and admin PDF.  These are safe command
# templates, not executable website controls. Do not include any credentials.
ADMIN_TERMINAL_CLI = [
    {
        "title": 'Master service health',
        "scope": 'Read-only · Ubuntu Master',
        "note": 'Inspect status and uptime without restarting any service.',
        "commands": [
            'systemctl is-active sysmonitor-web.service',
            'systemctl status sysmonitor-web.service --no-pager -l',
            'systemctl show sysmonitor-web.service -p ActiveState -p MainPID -p ActiveEnterTimestamp',
            'ss -ltn | grep ":8000 " || true',
        ],
    },
    {
        "title": 'Remote production health',
        "scope": 'Read-only · Remote production',
        "note": 'SSH runs on port 3048, not the master default port 22. These checks never change production.',
        "commands": [
            'ssh -p 3048 app-admin@103.16.152.238 "systemctl is-active sysmonitor-remote-web.service"',
            'ssh -p 3048 app-admin@103.16.152.238 "systemctl show sysmonitor-remote-web.service -p ActiveState -p MainPID"',
            'ssh -p 3048 app-admin@103.16.152.238 "git -C /home/app-admin/sysmonitor/repo rev-parse HEAD"',
        ],
    },
    {
        "title": 'Release identity & GitHub',
        "scope": 'Read-only · Release worktree',
        "note": 'Always check the clean release worktree; never reset, clean, or force-push the modified Desktop checkout.',
        "commands": [
            'git -C ~/security_staging/rc24_9_2_release_worktree status --porcelain',
            'git -C ~/security_staging/rc24_9_2_release_worktree rev-parse HEAD',
            'git -C ~/security_staging/rc24_9_2_release_worktree ls-remote origin refs/heads/main',
            'git -C ~/security_staging/rc24_9_2_release_worktree log -6 --format="%h %ci %s"',
            'git -C ~/security_staging/rc24_9_2_release_worktree diff --check',
        ],
    },
    {
        "title": 'Source difference & integrity',
        "scope": 'Read-only · Master',
        "note": 'Compare the running local source with the tested release copy before a change.',
        "commands": [
            'git -C ~/security_staging/rc24_9_2_release_worktree diff --stat',
            'git -C ~/security_staging/rc24_9_2_release_worktree diff --name-only',
            'sha256sum ~/Desktop/sysmonitor/shiftreport/services.py',
            'sha256sum ~/security_staging/rc24_9_2_release_worktree/sysmonitor/shiftreport/services.py',
            'git -C ~/security_staging/rc24_9_2_release_worktree ls-files | wc -l',
        ],
    },
    {
        "title": 'Django health & migrations',
        "scope": 'Read-only · Master venv',
        "note": 'Use checks without applying migrations to any real database.',
        "commands": [
            'cd ~/Desktop/sysmonitor && ./venv/bin/python manage.py check',
            'cd ~/Desktop/sysmonitor && ./venv/bin/python manage.py check --deploy',
            'cd ~/Desktop/sysmonitor && ./venv/bin/python manage.py showmigrations monitor shiftreport',
            'cd ~/Desktop/sysmonitor && ./venv/bin/python manage.py migrate --plan',
        ],
    },
    {
        "title": 'Isolated automated tests',
        "scope": 'Isolated test DB · Release',
        "note": 'Requires a test-safe environment. Django test creates/disposes an isolated test database; never run directly against live DB settings.',
        "commands": [
            'cd ~/security_staging/rc24_9_2_release_worktree/sysmonitor && ~/Desktop/sysmonitor/venv/bin/python manage.py test monitor shiftreport --noinput',
            'cd ~/security_staging/rc24_9_2_release_worktree/sysmonitor && ~/Desktop/sysmonitor/venv/bin/python manage.py test shiftreport --noinput',
        ],
    },
    {
        "title": 'Application logs & errors',
        "scope": 'Read-only · Both sites',
        "note": 'Journal logs may contain private operational data; redact before sharing.',
        "commands": [
            'journalctl -u sysmonitor-web.service -n 75 --no-pager',
            'journalctl -u sysmonitor-web.service --since "1 hour ago" --no-pager',
            'ssh -p 3048 app-admin@103.16.152.238 "journalctl -u sysmonitor-remote-web.service -n 75 --no-pager"',
            'systemctl --failed --no-pager',
        ],
    },
    {
        "title": 'Scheduled jobs & timers',
        "scope": 'Read-only · Both sites',
        "note": 'Inspect existing timer schedules, not job secrets or environment files.',
        "commands": [
            'systemctl list-timers --all --no-pager | grep -iE "sysmonitor|shift|pfe" || true',
            'systemctl list-units --type=service --all --no-pager | grep -i sysmonitor || true',
            'ssh -p 3048 app-admin@103.16.152.238 "systemctl list-timers --all --no-pager | grep -i sysmonitor || true"',
        ],
    },
    {
        "title": 'Network & reverse proxy',
        "scope": 'Read-only · Remote',
        "note": 'Inspect listeners and Nginx syntax; do not reveal certificates or private server variables.',
        "commands": [
            'ss -ltn',
            'ssh -p 3048 app-admin@103.16.152.238 "ss -ltn"',
            'ssh -p 3048 app-admin@103.16.152.238 "sudo nginx -t"',
        ],
    },
    {
        "title": 'Dependency inventory',
        "scope": 'Read-only · Both venvs',
        "note": 'Compare installed packages with tested requirements. Inventory is not an automated security audit.',
        "commands": [
            'cd ~/Desktop/sysmonitor && ./venv/bin/python -m pip list --format=columns',
            'cd ~/Desktop/sysmonitor && ./venv/bin/python -m pip check',
            'ssh -p 3048 app-admin@103.16.152.238 "cd /home/app-admin/sysmonitor/repo/sysmonitor && python3 -m pip --version"',
        ],
    },
    {
        "title": 'Backups & retention checks',
        "scope": 'Read-only · Both sites',
        "note": 'List only named rollback directories and export metadata; do not open, expose, or remove credentials/databases.',
        "commands": [
            'find ~/sysmonitor-backups -maxdepth 2 -type d -print | tail -40',
            'find ~/security_staging/final_exports -maxdepth 1 -name "SysMonitor_Source_*.tar.gz" -type f -printf "%f %s bytes\\n"',
            'ssh -p 3048 app-admin@103.16.152.238 "find ~/sysmonitor_rc26_rollbacks -maxdepth 2 -type d -print"',
        ],
    },
    {
        "title": 'Source archive checksum',
        "scope": 'Read-only · Master and Windows',
        "note": 'Never accept a printed PASS unless checksums of the actual downloaded files match.',
        "commands": [
            'cd ~/security_staging/final_exports && sha256sum -c SysMonitor_Source_f892ae7fbc0d.tar.gz.sha256',
            'Get-FileHash "C:\\Users\\USER\\Downloads\\SysMonitor_Source_f892ae7fbc0d.tar.gz" -Algorithm SHA256',
        ],
    },
    {
        "title": 'Controlled restart — change operation',
        "scope": 'Change · Maintenance window only',
        "note": 'Run only after verified backup and authorized regression. A restart can interrupt the portal; never restart other applications.',
        "commands": [
            'sudo systemctl restart sysmonitor-web.service',
            'systemctl is-active sysmonitor-web.service',
            'ssh -p 3048 app-admin@103.16.152.238 "sudo systemctl restart sysmonitor-remote-web.service"',
        ],
    },
    {
        "title": 'Release publication — change operation',
        "scope": 'Change · Explicit approval required',
        "note": 'The release scripts enforce exact-file scope, Git ancestry and preflight. Do not use git push --force, git reset --hard or git clean -fd.',
        "commands": [
            'bash ~/rc26_source_card/release_rc26.sh --publish',
            'bash ~/rc26_source_card/release_rc26.sh --deploy',
        ],
    },
]
SECURITY_RULES = [
    "Never include passwords, API tokens, device local keys, SMTP secrets, private recipient details, or other credentials in notes/screenshots/manuals.",
    "Use the least-privileged role required for the task. Page Access can reduce visibility but cannot increase a role's authority.",
    "Treat Administrative actions as change operations. Review the current state before changing configuration or restarting services.",
    "The remote mirror is for continuity/visibility; MASTER-owned monitoring and sensitive service controls remain protected.",
    "Exported reports may contain operational information. Share them only with their intended audience.",
    "Full Viewer may inspect reviewed configuration and operational pages with credentials masked; exporting, downloading, submitting, deleting and changing settings remain prohibited.",
]

TROUBLESHOOTING = [
    ("A page is missing", "The user's role or individual Page Access restriction may hide it. Ask an administrator to verify access."),
    ("A live value is blank", "Check whether the source is offline/stale. Blank can be safer and more correct than displaying cached data."),
    ("A report looks wrong", "Validate the underlying source entries and selected date/filter range before changing report code or formatting."),
    ("An event appears unexpected", "Read neighbouring events and timestamps to reconstruct the state transition. Activity and Events serve different purposes."),
    ("Remote view appears behind", "Allow for mirror/update timing, then compare the displayed version/state with MASTER before escalating."),
]
