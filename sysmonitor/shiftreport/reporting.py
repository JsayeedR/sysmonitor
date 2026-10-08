from io import BytesIO
import html
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from monitor.models import NotificationGateway

from .models import ShiftImportantIssue
from .services import BDT, parse_email_list


XLSX_MIME = (
    'application/vnd.openxmlformats-officedocument.'
    'spreadsheetml.sheet'
)


def _display_name(user):
    if user.get_full_name().strip():
        return user.get_full_name().strip()
    return user.username


def _autosize(ws):
    for column in ws.columns:
        max_len = 0
        letter = get_column_letter(column[0].column)

        for cell in column:
            value = '' if cell.value is None else str(cell.value)
            max_len = max(max_len, len(value))

        ws.column_dimensions[letter].width = min(max(max_len + 2, 10), 55)


def build_shift_report_xlsx(report):
    """
    Build the historical-issues attachment.

    The sheet structure follows the supplied Historical Important Issues.xlsx:

        Historical Data
        Category-1 | Category-2 | Details | Remarks | Event Type
    """
    wb = Workbook()

    ws = wb.active
    # Match the supplied Historical Important Issues.xlsx workbook.
    ws.title = 'Sheet1'

    ws.merge_cells('A1:E1')
    ws['A1'] = 'Historical Data'
    ws['A1'].font = Font(
        bold=True,
        size=14,
    )

    ws.append([
        'Category-1',
        'Category-2',
        'Details',
        'Remarks',
        'Event Type',
    ])

    for cell in ws[2]:
        cell.font = Font(
            bold=True,
        )

    issues = (
        ShiftImportantIssue.objects
        .all()
        .order_by('-opened_at', '-id')
    )

    for issue in issues:

        category_1 = (
            issue.category_1
            or ''
        )

        category_2 = (
            issue.category_2
            or issue.title
            or ''
        )

        details = (
            issue.description
            or ''
        )

        remarks = (
            issue.remarks
            or ''
        )

        event_type = (
            issue.event_type
            or issue.get_status_display()
        )

        ws.append([
            category_1,
            category_2,
            details,
            remarks,
            event_type,
        ])

    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(
                vertical='top',
                wrap_text=True,
            )

    _autosize(ws)

    stream = BytesIO()
    wb.save(stream)

    return (
        'Historical Important Issues.xlsx',
        stream.getvalue(),
    )

def build_recipient_lists(report, config):
    """
    Normal Shift Report:

      FROM = shared NOC Microsoft mailbox
      TO   = selected handover officer + fixed mandatory TO
      CC   = fixed mandatory CC + engineer's optional Additional CC

    manager_email is retained as a legacy mandatory TO address.
    """
    to_list = parse_email_list(
        '\n'.join([
            report.handover_to.email or '',
            config.mandatory_to or '',
            config.manager_email or '',
        ])
    )

    cc_list = parse_email_list(
        '\n'.join([
            config.mandatory_cc or '',
            report.additional_cc or '',
        ])
    )

    to_keys = {
        address.casefold()
        for address in to_list
    }

    cc_list = [
        address
        for address in cc_list
        if address.casefold() not in to_keys
    ]

    return to_list, cc_list


def build_mnoc_recipient_lists(config):
    to_list = parse_email_list(config.mnoc_to)
    cc_list = parse_email_list(config.mnoc_cc)

    to_keys = {x.casefold() for x in to_list}

    cc_list = [
        x
        for x in cc_list
        if x.casefold() not in to_keys
    ]

    return to_list, cc_list


def build_subject(report):
    return (
        f'SysMonitor NOC Shift Report — '
        f'{report.get_shift_display()} — '
        f'{report.report_date:%d %b %Y}'
    )


def _html_lines(value):
    return html.escape(
        value or ''
    ).replace('\n', '<br>')


def _outage_rows_html(report):
    rows = (
        report.outage_summary or {}
    ).get('rows', [])

    if not rows:
        return (
            '<div>No load shedding occurred during the shift.</div>'
        )

    body = []

    for row in rows:
        start = html.escape(
            str(row.get('start', ''))
        )
        end = html.escape(
            str(row.get('end', ''))
        )
        duration = html.escape(
            str(row.get('duration', ''))
        )
        generator = html.escape(
            str(row.get('generator', ''))
        )
        cycle_type = html.escape(
            str(row.get('cycle_type', ''))
        )

        body.append(
            '<tr>'
            f'<td>{start}</td>'
            f'<td>{end}</td>'
            f'<td>{duration}</td>'
            f'<td>{generator}</td>'
            f'<td>{cycle_type}</td>'
            '</tr>'
        )

    total = html.escape(
        str(
            (report.outage_summary or {})
            .get('total_duration', '0m')
        )
    )

    return (
        '<table style="width:100%;border-collapse:collapse;margin:4px 0">'
        '<tr>'
        '<th style="border:1px solid #777;padding:4px">Start</th>'
        '<th style="border:1px solid #777;padding:4px">End</th>'
        '<th style="border:1px solid #777;padding:4px">Duration</th>'
        '<th style="border:1px solid #777;padding:4px">GEN</th>'
        '<th style="border:1px solid #777;padding:4px">Type</th>'
        '</tr>'
        + ''.join(body)
        + '</table>'
        f'<strong>Total Outage Duration:</strong> {total}'
    )


def _activity_rows_html(report):
    rows = list(
        report.activity_rows.all()
    )

    if not rows:
        return (
            '<tr>'
            '<td style="border:1px solid #777;padding:6px">-</td>'
            '<td style="border:1px solid #777;padding:6px">-</td>'
            '<td style="border:1px solid #777;padding:6px">-</td>'
            '<td style="border:1px solid #777;padding:6px">-</td>'
            '<td style="border:1px solid #777;padding:6px">-</td>'
            '</tr>'
        )

    result = []

    for row in rows:
        result.append(
            '<tr>'
            f'<td style="border:1px solid #777;padding:6px;vertical-align:top">'
            f'{_html_lines(row.activity_type)}</td>'
            f'<td style="border:1px solid #777;padding:6px;vertical-align:top">'
            f'{_html_lines(row.client_vendor)}</td>'
            f'<td style="border:1px solid #777;padding:6px;vertical-align:top">'
            f'{_html_lines(row.details)}</td>'
            f'<td style="border:1px solid #777;padding:6px;vertical-align:top">'
            f'{_html_lines(row.status)}</td>'
            f'<td style="border:1px solid #777;padding:6px;vertical-align:top">'
            f'{_html_lines(row.remarks)}</td>'
            '</tr>'
        )

    return ''.join(result)


def build_html_body(report, config):
    """
    Final Shift Report email body.

    Its structure intentionally follows the operational Morning/Evening/Night
    report examples:
      Dear Sir
      A. Shift Details
      B. Regular Shift Activities
      C. Shift Activities and the Issues
      Automatic Prepared By signature
    """
    from .services import (
        automatic_temperature_text,
    )

    signature = build_prepared_by_signature(
        report.prepared_by,
        config,
    )

    temp_text = automatic_temperature_text(
        report.sensor_summary or {}
    )

    shift_name = html.escape(
        report.get_shift_display().replace(
            ' Shift',
            '',
        )
    )

    handover = html.escape(
        report.handover_to.name
    )

    prepared_by = html.escape(
        _display_name(report.prepared_by)
    )

    return f"""
    <html>
      <body style="font-family:Calibri,Arial,sans-serif;font-size:11pt">

        <p>Dear Sir,</p>

        <p>
          Please find the report in the below table along with the
          historical issues attached:
        </p>

        <table
          style="
            width:100%;
            border-collapse:collapse;
            border:1px solid #555;
          "
        >
          <tr>
            <th
              colspan="2"
              style="
                border:1px solid #555;
                padding:7px;
                text-align:left;
                background:#c6d9f1;
                font-size:12pt;
              "
            >
              COXCLS NOC SHIFT REPORT
            </th>
          </tr>

          <tr>
            <th
              colspan="2"
              style="
                border:1px solid #555;
                padding:7px;
                text-align:left;
                background:#eef3f8;
              "
            >
              A. Shift Details
            </th>
          </tr>

          <tr>
            <td style="border:1px solid #555;padding:6px;width:26%">
              <strong>Date:</strong>
            </td>
            <td style="border:1px solid #555;padding:6px">
              {report.report_date:%d-%m-%Y}
            </td>
          </tr>

          <tr>
            <td style="border:1px solid #555;padding:6px">
              <strong>Shift:</strong>
            </td>
            <td style="border:1px solid #555;padding:6px">
              {shift_name}
            </td>
          </tr>

          <tr>
            <td style="border:1px solid #555;padding:6px">
              <strong>Prepared By:</strong>
            </td>
            <td style="border:1px solid #555;padding:6px">
              {prepared_by}
            </td>
          </tr>

          <tr>
            <td style="border:1px solid #555;padding:6px">
              <strong>Handover To:</strong>
            </td>
            <td style="border:1px solid #555;padding:6px">
              {handover}
            </td>
          </tr>

          <tr>
            <th
              colspan="2"
              style="
                border:1px solid #555;
                padding:7px;
                text-align:left;
                background:#c6e0b4;
              "
            >
              B. Regular Shift Activities
            </th>
          </tr>

          <tr>
            <td style="border:1px solid #555;padding:6px">
              <strong>AC Shifting</strong>
            </td>
            <td style="border:1px solid #555;padding:6px">
              {_html_lines(report.ac_shifting)}
            </td>
          </tr>

          <tr>
            <td style="border:1px solid #555;padding:6px">
              <strong>Network</strong>
            </td>
            <td style="border:1px solid #555;padding:6px">
              {_html_lines(report.network_status)}
            </td>
          </tr>

          <tr>
            <td style="border:1px solid #555;padding:6px">
              <strong>Cable</strong>
            </td>
            <td style="border:1px solid #555;padding:6px">
              {_html_lines(report.cable_status)}
            </td>
          </tr>

          <tr>
            <td style="border:1px solid #555;padding:6px">
              <strong>PFE</strong>
            </td>
            <td style="border:1px solid #555;padding:6px">
              {_html_lines(report.pfe_status)}
            </td>
          </tr>

          <tr>
            <td style="border:1px solid #555;padding:6px">
              <strong>DWDM</strong>
            </td>
            <td style="border:1px solid #555;padding:6px">
              {_html_lines(report.dwdm_status)}
            </td>
          </tr>

          <tr>
            <td style="border:1px solid #555;padding:6px">
              <strong>This Month's Maintenance Activity</strong>
            </td>
            <td style="border:1px solid #555;padding:6px">
              {_html_lines(report.maintenance_activity)}
            </td>
          </tr>

          <tr>
            <td style="border:1px solid #555;padding:6px">
              <strong>Generator Status</strong>
            </td>
            <td style="border:1px solid #555;padding:6px">
              {_html_lines(report.generator_status_text)}
            </td>
          </tr>

          <tr>
            <td style="border:1px solid #555;padding:6px">
              <strong>Load Shedding</strong>
            </td>
            <td style="border:1px solid #555;padding:6px">
              {_outage_rows_html(report)}
            </td>
          </tr>

          <tr>
            <td style="border:1px solid #555;padding:6px">
              <strong>Colocation Room Temperature</strong>
            </td>
            <td style="border:1px solid #555;padding:6px">
              {_html_lines(temp_text)}
            </td>
          </tr>

          <tr>
            <td style="border:1px solid #555;padding:6px">
              <strong>Rain Water Leakage</strong>
            </td>
            <td style="border:1px solid #555;padding:6px">
              {_html_lines(report.rain_water_leakage)}
            </td>
          </tr>

          <tr>
            <td style="border:1px solid #555;padding:6px">
              <strong>SMW4 CIRCUIT &amp; BANDWIDTH STATUS</strong>
            </td>
            <td style="border:1px solid #555;padding:6px">
              {_html_lines(report.bandwidth_status)}
            </td>
          </tr>
        </table>

        <br>

        <table
          style="
            width:100%;
            border-collapse:collapse;
            border:1px solid #555;
          "
        >
          <tr>
            <th
              colspan="5"
              style="
                border:1px solid #555;
                padding:7px;
                text-align:left;
                background:#eef3f8;
              "
            >
              C. Shift Activities and the Issues.
            </th>
          </tr>

          <tr>
            <th style="border:1px solid #555;padding:6px">Type</th>
            <th style="border:1px solid #555;padding:6px">Client/Vendor</th>
            <th style="border:1px solid #555;padding:6px">
              Details of the issue
            </th>
            <th style="border:1px solid #555;padding:6px">Status</th>
            <th style="border:1px solid #555;padding:6px">Remarks</th>
          </tr>

          {_activity_rows_html(report)}
        </table>

        <p>
          -------------------------------------------<br>
          Regards,<br>
          {signature}
        </p>

      </body>
    </html>
    """

def build_mnoc_html_body(report):
    previous = report.previous_day_summary or {}
    outage = previous.get('outage', {})

    return f"""
    <html>
      <body>
        <h2>SysMonitor MNOC Night Shift Report</h2>

        <p>
          <strong>Night Shift Date:</strong>
          {report.report_date:%d %b %Y}
        </p>

        <p>
          <strong>Previous Calendar Day:</strong>
          {html.escape(str(previous.get('date', '—')))}<br>
          <strong>Previous-day outage count:</strong>
          {outage.get('count', 0)}<br>
          <strong>Previous-day total outage:</strong>
          {html.escape(str(outage.get('total_duration', '0m')))}
        </p>

        <h3>MNOC Notes</h3>
        <p>
          {html.escape(report.mnoc_notes or '—').replace(chr(10), '<br>')}
        </p>
      </body>
    </html>
    """


def send_smtp_email(
    *,
    subject,
    html_body,
    to_list,
    cc_list=None,
    attachment=None,
    shift_config=None,
):
    """
    Send through the existing SysMonitor Email NotificationGateway.

    attachment:
        (filename, bytes, mime_type)

    This function performs the real network send. Tests must mock SMTP.
    """
    cc_list = cc_list or []

    if not to_list:
        raise ValueError('No TO recipient configured.')

    if shift_config is not None:
        host = (shift_config.smtp_host or '').strip()
        port = shift_config.smtp_port
        username = (shift_config.smtp_username or '').strip()
        password = shift_config.smtp_password or ''
        sender = (
            (shift_config.noc_from_email or '').strip()
            or username
        )
        from_name = (
            shift_config.noc_from_name
            or 'SMW4 NOC (COXCLS)'
        ).strip()
        use_tls = bool(shift_config.smtp_use_tls)

        if not host:
            raise RuntimeError(
                'Shift Report SMTP host is not configured.'
            )

        if not username:
            raise RuntimeError(
                'Shift Report SMTP username is not configured.'
            )

        if not password:
            raise RuntimeError(
                'Shift Report SMTP password is not configured.'
            )

        if not sender:
            raise RuntimeError(
                'Shift Report NOC From email is not configured.'
            )

    else:
        try:
            gateway = NotificationGateway.objects.get(
                channel='email'
            )
        except NotificationGateway.DoesNotExist:
            raise RuntimeError(
                'Email Notification Gateway is not configured.'
            )

        if not gateway.is_enabled:
            raise RuntimeError(
                'Email Notification Gateway is disabled.'
            )

        host = gateway.email_host
        port = gateway.email_port
        username = gateway.email_username
        password = gateway.email_password
        sender = (
            gateway.email_from.strip()
            or gateway.email_username.strip()
        )
        from_name = ''
        use_tls = True

    msg = MIMEMultipart()

    msg['Subject'] = subject

    if from_name:
        msg['From'] = f'{from_name} <{sender}>'
    else:
        msg['From'] = sender
    msg['To'] = ', '.join(to_list)

    if cc_list:
        msg['Cc'] = ', '.join(cc_list)

    msg.attach(
        MIMEText(
            html_body,
            'html',
            'utf-8',
        )
    )

    if attachment:
        filename, content, mime_type = attachment

        part = MIMEApplication(
            content,
            Name=filename,
        )

        part.add_header(
            'Content-Disposition',
            'attachment',
            filename=filename,
        )

        if mime_type:
            part.replace_header(
                'Content-Type',
                mime_type,
            )

        msg.attach(part)

    recipients = list(to_list) + list(cc_list)

    smtp = smtplib.SMTP(
        host,
        port,
        timeout=30,
    )

    try:
        smtp.ehlo()

        if use_tls:
            smtp.starttls()
            smtp.ehlo()

        smtp.login(
            username,
            password,
        )

        smtp.sendmail(
            sender,
            recipients,
            msg.as_string(),
        )

    finally:
        try:
            smtp.quit()
        except Exception:
            pass


def build_prepared_by_signature(user, config):
    """
    Signature is generated automatically from Prepared By.
    """
    name = _display_name(user)

    designation = ''
    mobile = ''

    try:
        designation = user.userprofile.designation or ''
        mobile = user.userprofile.mobile_number or ''
    except Exception:
        pass

    lines = [
        html.escape(name),
    ]

    if designation:
        lines.append(
            html.escape(designation)
        )

    if config.company_name:
        lines.append(
            html.escape(config.company_name)
        )

    if mobile:
        lines.append(
            f'Mob/WhatsApp# {html.escape(mobile)}'
        )

    if config.noc_mobile:
        lines.append(
            f'NOC Mob/WhatsApp# {html.escape(config.noc_mobile)}'
        )

    if config.website:
        lines.append(
            f'Website# {html.escape(config.website)}'
        )

    return '<br>'.join(lines)


def build_pfe_subject():
    return (
        'Re: COX/SATUN/TUS CLS # Daily Basis PFE Readings'
    )


def build_pfe_html_body(report, config):
    """
    Match the existing MNOC daily PFE email structure.
    """
    signature = build_prepared_by_signature(
        report.prepared_by,
        config,
    )

    return f"""
    <html>
      <body>
        <p>Dear MNOC,</p>

        <p>
          Please find the daily PFE reading from COX CLS
          for your kind perusal.
        </p>

        <table
          border="1"
          cellpadding="7"
          cellspacing="0"
          style="border-collapse:collapse;text-align:center"
        >
          <thead>
            <tr>
              <th>Date</th>
              <th>Voltage(V)</th>
              <th>Current (mA)</th>
              <th>Mode</th>
              <th>Remark</th>
              <th>Alarm Status</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{report.report_date:%d-%m-%Y}</td>
              <td>{html.escape(str(report.voltage_v))}</td>
              <td>{html.escape(str(report.current_ma))}</td>
              <td>{html.escape(report.mode)}</td>
              <td>{html.escape(report.remark)}</td>
              <td>{html.escape(report.alarm_status)}</td>
            </tr>
          </tbody>
        </table>

        <p>
          -------------------------------------------<br>
          Regards,<br>
          {signature}
        </p>
      </body>
    </html>
    """
