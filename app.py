import os
import uuid
import time
import json
from datetime import datetime, timedelta
from flask import Flask, request, render_template_string, session, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash

# --- GOOGLE SHEETS SETUP (VERCEL SECURE) ---
try:
    import gspread
    from google.oauth2.service_account import Credentials
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    
    sheet_id = os.environ.get('SHEET_ID')
    google_creds_json = os.environ.get('GOOGLE_CREDENTIALS')

    if sheet_id and google_creds_json:
        creds_dict = json.loads(google_creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(creds)
        google_sheet = client.open_by_key(sheet_id)
        USE_SHEETS = True
        print("✅ Connected to Google Sheets!")
    else:
        USE_SHEETS = False
        print("⚠️ Environment variables missing. Running in Local Memory Mode.")
except Exception as e:
    USE_SHEETS = False
    print(f"⚠️ Google Sheets setup failed: {e}")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "super_secure_ultimate_portal_key")
app.permanent_session_lifetime = timedelta(days=365) 

# --- IN-MEMORY DATABASE & SYNC LOGIC ---
db = {
    "schools": {},   
    "classes": {},   
    "teachers": {},  
    "messages": [],  
    "active_days": {}
}

LAST_SYNC_TIME = 0
SYNC_COOLDOWN = 10 # 10 seconds between automatic syncs

def save_sheet(sheet_name, headers, rows):
    if not USE_SHEETS: return
    try:
        try:
            sheet = google_sheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            sheet = google_sheet.add_worksheet(title=sheet_name, rows="1000", cols=str(len(headers)))
        sheet.clear()
        try:
            sheet.update(values=[headers] + rows, range_name="A1")
        except TypeError: 
            sheet.update([headers] + rows)
    except Exception as e:
        print(f"Failed to sync to {sheet_name}: {e}")

def append_sheet_row(sheet_name, row):
    """ ATOMIC APPEND - Prevents concurrent user overwrites """
    if not USE_SHEETS: return
    try:
        sheet = google_sheet.worksheet(sheet_name)
        sheet.append_row(row, value_input_option='USER_ENTERED')
    except Exception as e:
        print(f"Failed to append row to {sheet_name}: {e}")

def save_schools():
    rows = [[sid, s['name'], s['created_by']] for sid, s in db["schools"].items()]
    save_sheet('Schools', ['s_id', 'name', 'created_by'], rows)

def save_classes():
    rows = [[cid, c['school_id'], c['name'], c['password_hash']] for cid, c in db["classes"].items()]
    save_sheet('Classes', ['c_id', 'school_id', 'name', 'password_hash'], rows)

def save_teachers():
    rows = [[tid, t['school_id'], t['username'], t['password_hash'], t['subject'], str(t['is_admin']), str(t['is_approved']), db['active_days'].get(tid, 'Day-1')] for tid, t in db["teachers"].items()]
    save_sheet('Teachers', ['t_id', 'school_id', 'username', 'password_hash', 'subject', 'is_admin', 'is_approved', 'active_day'], rows)

def save_messages():
    """ Used only for mass updates (deletions/pinning). Sending uses append_sheet_row. """
    rows = [[m['id'], m['school_id'], m['class_id'], m['teacher_id'], m['student_id'], m['text'], str(m['is_top']), m['day_id'], m['timestamp'], m['iso_time']] for m in db["messages"]]
    save_sheet('Messages', ['id', 'school_id', 'class_id', 'teacher_id', 'student_id', 'text', 'is_top', 'day_id', 'timestamp', 'iso_time'], rows)

def load_data(force=False):
    global LAST_SYNC_TIME
    if not USE_SHEETS: return
    
    current_time = time.time()
    if not force and (current_time - LAST_SYNC_TIME < SYNC_COOLDOWN):
        return 
        
    try:
        temp_schools, temp_classes, temp_teachers, temp_active_days, temp_msgs = {}, {}, {}, {}, []
        
        try:
            for r in google_sheet.worksheet("Schools").get_all_records():
                temp_schools[str(r['s_id'])] = {"name": str(r['name']), "created_by": str(r['created_by'])}
        except Exception: pass
        
        try:
            for r in google_sheet.worksheet("Classes").get_all_records():
                temp_classes[str(r['c_id'])] = {"school_id": str(r['school_id']), "name": str(r['name']), "password_hash": str(r['password_hash'])}
        except Exception: pass
        
        try:
            for r in google_sheet.worksheet("Teachers").get_all_records():
                tid = str(r['t_id'])
                temp_teachers[tid] = {
                    "school_id": str(r['school_id']), "username": str(r['username']), 
                    "password_hash": str(r['password_hash']), "subject": str(r['subject']),
                    "is_admin": str(r['is_admin']).lower() == 'true', "is_approved": str(r['is_approved']).lower() == 'true'
                }
                temp_active_days[tid] = str(r.get('active_day', 'Day-1'))
        except Exception: pass
        
        try:
            for r in google_sheet.worksheet("Messages").get_all_records():
                temp_msgs.append({
                    "id": str(r['id']), "school_id": str(r['school_id']), "class_id": str(r['class_id']),
                    "teacher_id": str(r['teacher_id']), "student_id": str(r['student_id']),
                    "text": str(r['text']), "is_top": str(r['is_top']).lower() == 'true',
                    "day_id": str(r['day_id']), "timestamp": str(r['timestamp']), "iso_time": str(r['iso_time'])
                })
        except Exception: pass
        
        db["schools"] = temp_schools
        db["classes"] = temp_classes
        db["teachers"] = temp_teachers
        db["active_days"] = temp_active_days
        db["messages"] = temp_msgs
        LAST_SYNC_TIME = current_time
    except Exception as e:
        print("⚠️ Error loading data:", e)

@app.before_request
def before_request():
    load_data(force=False)

load_data(force=True)

# --- HTML TEMPLATES ---
BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>EduPortal Pro</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <style>
        :root { --primary: #4f46e5; --secondary: #10b981; --dark: #0f172a; --light: #f1f5f9; }
        body { background-color: var(--light); font-family: 'Inter', sans-serif; padding-top: 15px; padding-bottom: 50px; }
        
        /* Navbar & Navigations */
        .navbar { background: linear-gradient(135deg, var(--primary), #3730a3); border-radius: 12px; padding: 1rem 1.5rem; margin-bottom: 2rem; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }
        .navbar-brand { font-weight: 800; color: white !important; font-size: 1.5rem; letter-spacing: -0.5px; }
        .nav-btn { font-weight: 600; border-radius: 8px; margin-left: 8px; }
        
        /* Cards & UI Elements */
        .card { border: none; border-radius: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); background: white; transition: transform 0.2s; }
        .form-control, .form-select { border-radius: 10px; padding: 0.8rem 1rem; border: 1px solid #cbd5e1; }
        .form-control:focus, .form-select:focus { border-color: var(--primary); box-shadow: 0 0 0 4px rgba(79, 70, 229, 0.1); }
        .btn { border-radius: 10px; font-weight: 600; padding: 0.7rem 1.2rem; transition: all 0.2s; }
        .btn:hover { transform: translateY(-1px); box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
        
        /* Message Box Universal Sizing & Text Wrap */
        .msg-box { background: white; padding: 1.2rem; border-radius: 12px; border-left: 5px solid var(--primary); margin-bottom: 1rem; box-shadow: 0 2px 8px rgba(0,0,0,0.04); word-wrap: break-word; overflow-wrap: break-word; }
        .msg-top { border-left: 5px solid #f59e0b; background: #fffbeb; }
        .msg-text { white-space: pre-wrap; margin-bottom: 0.5rem; color: #1e293b; font-size: 1.1rem; line-height: 1.5; }
        
        /* Mobile Specific Overrides */
        @media (max-width: 768px) {
            .nav-btn { margin-left: 0; margin-bottom: 10px; display: block; width: 100%; text-align: center; }
            .btn-mobile-full { width: 100% !important; margin-bottom: 0.5rem; }
            .msg-actions { width: 100%; display: flex; gap: 0.5rem; flex-direction: column; mt-3 }
            h1 { font-size: 1.8rem; }
        }
    </style>
    <script>
        document.addEventListener("DOMContentLoaded", function() {
            // Converts UTC exactly to user's local timezone & preferred format
            document.querySelectorAll('.local-time').forEach(el => {
                let iso = el.getAttribute('data-iso');
                if(iso) {
                    let date = new Date(iso);
                    let options = { month: 'short', day: 'numeric', hour: '2-digit', minute:'2-digit' };
                    el.innerHTML = '<i class="fa-regular fa-clock me-1"></i>' + date.toLocaleDateString(undefined, options);
                }
            });
        });
    </script>
</head>
<body>
    <div class="container">
        <nav class="navbar navbar-expand-lg">
            <div class="container-fluid px-0">
                <a class="navbar-brand" href="/"><i class="fa-solid fa-graduation-cap me-2"></i>EduPortal</a>
                <button class="navbar-toggler border-0 shadow-none text-white" type="button" data-bs-toggle="collapse" data-bs-target="#navMenu"><i class="fa-solid fa-bars fa-lg"></i></button>
                <div class="collapse navbar-collapse justify-content-end mt-3 mt-lg-0" id="navMenu">
                    <a href="/legend" class="btn btn-warning nav-btn text-dark"><i class="fa-solid fa-star me-1"></i>Legend Board</a>
                    {% if session.get('teacher_id') %}
                        {% if session.get('is_admin') %}
                            <a href="/admin_dashboard" class="btn btn-dark nav-btn"><i class="fa-solid fa-gear me-1"></i>Admin</a>
                        {% endif %}
                        <a href="/teacher_dashboard" class="btn btn-light nav-btn"><i class="fa-solid fa-chalkboard-user me-1"></i>Dashboard</a>
                        <a href="/logout" class="btn btn-danger nav-btn"><i class="fa-solid fa-right-from-bracket"></i></a>
                    {% elif session.get('student_class_id') %}
                        <a href="/student_portal" class="btn btn-light nav-btn"><i class="fa-solid fa-paper-plane me-1"></i>Portal</a>
                        <a href="/logout" class="btn btn-danger nav-btn"><i class="fa-solid fa-right-from-bracket"></i> Exit</a>
                    {% endif %}
                </div>
            </div>
        </nav>
        
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="alert alert-{{ 'success' if category == 'message' else category }} alert-dismissible fade show rounded-4 shadow-sm border-0">
                <span class="fw-bold">{{ message }}</span>
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
              </div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        {% block content %}{% endblock %}
    </div>
</body>
</html>
"""

INDEX_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="row text-center mt-2 mt-md-4 px-2">
    <div class="col-12 mb-4 mb-md-5"><h1 class="fw-bold text-dark">School Communication Portal</h1><p class="text-muted fs-5">Fast, secure, and organized messaging.</p></div>
    <div class="col-md-4 mb-4"><div class="card h-100 p-4 p-md-5 border-top border-4 border-primary">
        <i class="fa-solid fa-user-graduate fa-3x text-primary mb-3"></i><h3 class="fw-bold">Students</h3>
        <p class="text-muted mb-4">Select school & class to safely message your teachers.</p>
        <a href="/student_auth" class="btn btn-primary w-100 mt-auto btn-lg">Join Class</a>
    </div></div>
    <div class="col-md-4 mb-4"><div class="card h-100 p-4 p-md-5 border-top border-4 border-success">
        <i class="fa-solid fa-chalkboard-teacher fa-3x text-success mb-3"></i><h3 class="fw-bold">Teachers</h3>
        <p class="text-muted mb-4">Login to view student messages and pin important notes.</p>
        <a href="/teacher_login" class="btn btn-success w-100 mt-auto btn-lg">Teacher Portal</a>
    </div></div>
    <div class="col-md-4 mb-4"><div class="card h-100 p-4 p-md-5 border-top border-4 border-dark">
        <i class="fa-solid fa-building-columns fa-3x text-dark mb-3"></i><h3 class="fw-bold">Admin</h3>
        <p class="text-muted mb-4">Register your school portal and manage staff efficiently.</p>
        <a href="/create_school" class="btn btn-dark w-100 mt-auto btn-lg">Create School</a>
    </div></div>
</div>
""")

CREATE_SCHOOL_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="row justify-content-center px-2"><div class="col-md-7 col-lg-5"><div class="card p-4 p-md-5 border-top border-4 border-dark">
    <h3 class="text-center fw-bold mb-4">Register New School</h3>
    <form action="/create_school" method="POST" autocomplete="off">
        <div class="form-floating mb-4">
            <input type="text" name="school_name" class="form-control" id="schoolName" placeholder="School Name" required>
            <label for="schoolName">School Name</label>
        </div>
        <hr class="mb-4 text-muted">
        <h5 class="fw-bold text-primary mb-3">Admin Account Setup</h5>
        <div class="form-floating mb-3">
            <input type="text" name="username" class="form-control" id="adminUser" placeholder="Username" autocomplete="off" required>
            <label for="adminUser">Admin Username</label>
        </div>
        <div class="form-floating mb-3">
            <input type="text" name="subject" class="form-control" id="adminRole" placeholder="Role" required>
            <label for="adminRole">Subject / Role</label>
        </div>
        <div class="form-floating mb-4">
            <input type="password" name="password" class="form-control" id="adminPw" placeholder="Password" autocomplete="new-password" required minlength="6">
            <label for="adminPw">Password</label>
        </div>
        <button type="submit" class="btn btn-dark w-100 btn-lg">Create Portal</button>
    </form>
</div></div></div>
""")

TEACHER_LOGIN_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="row justify-content-center px-2"><div class="col-md-7 col-lg-5"><div class="card p-4 p-md-5 border-top border-4 border-success">
    <div class="text-center mb-4">
        <i class="fa-solid fa-chalkboard-user fa-3x text-success mb-3"></i>
        <h3 class="fw-bold">Teacher Login</h3>
    </div>
    <form action="/teacher_login" method="POST">
        <div class="form-floating mb-3">
            <select name="school_id" class="form-select" id="schoolSelect" required>
                <option value="" disabled selected>Select your school</option>
                {% for s_id, s_info in schools.items() %}<option value="{{ s_id }}">{{ s_info.name }}</option>{% endfor %}
            </select>
            <label for="schoolSelect">School</label>
        </div>
        <div class="form-floating mb-3">
            <input type="text" name="username" class="form-control" id="username" placeholder="Username" required autocomplete="username">
            <label for="username">Username</label>
        </div>
        <div class="form-floating mb-4">
            <input type="password" name="password" class="form-control" id="password" placeholder="Password" required autocomplete="current-password">
            <label for="password">Password</label>
        </div>
        <button type="submit" class="btn btn-success w-100 btn-lg fw-bold">Login to Dashboard</button>
    </form>
    
    <div class="mt-4 pt-4 border-top text-center">
        <p class="text-muted small mb-3">Don't have an account?</p>
        <button class="btn btn-outline-primary w-100 fw-bold shadow-sm" data-bs-toggle="modal" data-bs-target="#registerModal">Register as New Teacher</button>
    </div>
</div></div></div>

<!-- Premium Registration Modal -->
<div class="modal fade" id="registerModal" tabindex="-1" aria-hidden="true">
  <div class="modal-dialog modal-dialog-centered">
    <div class="modal-content border-0 rounded-4 shadow-lg">
      <div class="modal-header bg-primary text-white rounded-top-4 border-0">
        <h5 class="modal-title fw-bold"><i class="fa-solid fa-user-plus me-2"></i>Teacher Registration</h5>
        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
      </div>
      <div class="modal-body p-4 p-md-5">
        <div class="alert alert-info py-2 small fw-bold mb-4"><i class="fa-solid fa-shield-halved me-1"></i> Admin approval is required after registration.</div>
        <form action="/teacher_register" method="POST" autocomplete="off">
            <div class="form-floating mb-3">
                <select name="school_id" class="form-select" id="regSchool" required>
                    <option value="" disabled selected>Select School</option>
                    {% for s_id, s_info in schools.items() %}<option value="{{ s_id }}">{{ s_info.name }}</option>{% endfor %}
                </select>
                <label for="regSchool">School</label>
            </div>
            <div class="form-floating mb-3">
                <input type="text" name="username" class="form-control" id="regUser" placeholder="e.g. Mr. Smith" autocomplete="off" required>
                <label for="regUser">Username</label>
            </div>
            <div class="form-floating mb-3">
                <input type="text" name="subject" class="form-control" id="regSubj" placeholder="e.g. Math" required>
                <label for="regSubj">Subject / Department</label>
            </div>
            <div class="form-floating mb-4">
                <input type="password" name="password" class="form-control" id="regPw" placeholder="Min 6 characters" required minlength="6" autocomplete="new-password">
                <label for="regPw">Password</label>
            </div>
            <div class="d-flex gap-2">
                <button type="button" class="btn btn-light border flex-fill" data-bs-dismiss="modal">Cancel</button>
                <button type="submit" class="btn btn-primary flex-fill fw-bold shadow-sm">Submit Request</button>
            </div>
        </form>
      </div>
    </div>
  </div>
</div>
""")

ADMIN_DASH_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="card p-3 p-md-4 mb-4 bg-dark text-white border-0 shadow">
    <div class="d-flex flex-column flex-md-row justify-content-between align-items-md-center gap-3">
        <h2 class="fw-bold m-0 fs-3"><i class="fa-solid fa-shield-halved text-warning me-2"></i>Admin Panel</h2>
        <span class="badge bg-light text-dark fs-6">{{ school.name }}</span>
    </div>
</div>

<ul class="nav nav-tabs mb-4 border-0 flex-nowrap overflow-auto hide-scrollbar" id="adminTabs" role="tablist">
  <li class="nav-item"><button class="nav-link active btn btn-light me-2 fw-bold rounded shadow-sm text-nowrap" data-bs-toggle="tab" data-bs-target="#classes">Classes</button></li>
  <li class="nav-item"><button class="nav-link btn btn-light me-2 fw-bold rounded shadow-sm text-nowrap" data-bs-toggle="tab" data-bs-target="#teachers">Teachers</button></li>
  <li class="nav-item"><button class="nav-link btn btn-light fw-bold rounded shadow-sm text-nowrap" data-bs-toggle="tab" data-bs-target="#settings">Settings</button></li>
</ul>

<div class="tab-content">
    <div class="tab-pane fade show active" id="classes">
        <div class="row">
            <div class="col-xl-4 mb-4">
                <div class="card p-4 border-top border-4 border-primary">
                    <h5 class="fw-bold mb-3">Add New Class</h5>
                    <form action="/admin/add_class" method="POST" autocomplete="off">
                        <input type="text" name="class_name" class="form-control mb-3" required placeholder="Class Name (e.g. 10th Grade)">
                        <input type="text" name="class_password" class="form-control mb-4" required placeholder="Entry Password" autocomplete="new-password">
                        <button class="btn btn-primary w-100 fw-bold">Create Class</button>
                    </form>
                </div>
            </div>
            <div class="col-xl-8 mb-4">
                <div class="card p-4">
                    <h5 class="fw-bold mb-3">Manage Classes</h5>
                    {% for c_id, c_info in classes.items() %}
                    <div class="d-flex flex-column flex-md-row justify-content-between align-items-md-center p-3 border rounded-3 mb-3 bg-light gap-3">
                        <span class="fw-bold fs-5 text-dark"><i class="fa-solid fa-users text-primary me-2"></i>{{ c_info.name }}</span>
                        <div class="d-flex flex-column flex-md-row gap-2 w-100 w-md-auto">
                            <form action="/admin/change_class_pw/{{ c_id }}" method="POST" class="d-flex m-0 flex-grow-1">
                                <input type="text" name="new_password" class="form-control form-control-sm me-2" placeholder="New Password" required autocomplete="new-password">
                                <button class="btn btn-sm btn-dark fw-bold text-nowrap">Change PW</button>
                            </form>
                            <form action="/admin/delete_class/{{ c_id }}" method="POST" class="m-0" onsubmit="return confirm('Delete this class entirely?');">
                                <button class="btn btn-sm btn-danger fw-bold w-100"><i class="fa-solid fa-trash me-md-1"></i><span class="d-inline d-md-none"> Delete</span></button>
                            </form>
                        </div>
                    </div>
                    {% else %}<div class="alert alert-light text-center border text-muted py-4">No classes created yet.</div>{% endfor %}
                </div>
            </div>
        </div>
    </div>

    <div class="tab-pane fade" id="teachers">
        <div class="row">
            <div class="col-12 mb-4">
                <div class="card p-4 border-top border-4 border-warning shadow-sm">
                    <h5 class="fw-bold mb-3 text-warning"><i class="fa-solid fa-clock-rotate-left me-2"></i>Pending Approvals</h5>
                    {% for t_id, t_info in pending_teachers.items() %}
                    <div class="d-flex flex-column flex-md-row justify-content-between align-items-md-center p-3 border rounded mb-2 bg-white gap-3">
                        <span><b class="fs-5">{{ t_info.username }}</b> <span class="badge bg-light text-dark ms-2">{{ t_info.subject }}</span></span>
                        <div class="d-flex gap-2 w-100 w-md-auto">
                            <form action="/admin/action_teacher/{{ t_id }}/approve" method="POST" class="m-0 flex-fill"><button class="btn btn-success w-100 fw-bold shadow-sm">Approve</button></form>
                            <form action="/admin/action_teacher/{{ t_id }}/reject" method="POST" class="m-0 flex-fill"><button class="btn btn-danger w-100 fw-bold shadow-sm">Reject</button></form>
                        </div>
                    </div>
                    {% else %}<p class="text-muted m-0 small">No pending requests.</p>{% endfor %}
                </div>
            </div>
            <div class="col-12">
                <div class="card p-4 shadow-sm">
                    <h5 class="fw-bold mb-3">Approved Staff</h5>
                    <div class="table-responsive">
                    <table class="table table-hover align-middle m-0">
                        <thead class="table-light"><tr><th>Staff Member</th><th>Subject</th><th>Role</th><th class="text-end">Actions</th></tr></thead>
                        <tbody>
                            {% for t_id, t_info in approved_teachers.items() %}
                            <tr>
                                <td class="fw-bold text-nowrap">{{ t_info.username }}</td>
                                <td>{{ t_info.subject }}</td>
                                <td>{% if t_info.is_admin %}<span class="badge bg-dark">Admin</span>{% else %}<span class="badge bg-secondary">Teacher</span>{% endif %}</td>
                                <td class="text-end">
                                    {% if t_id != session['teacher_id'] %}
                                        <div class="d-flex gap-1 justify-content-end flex-wrap">
                                        <form action="/admin/action_teacher/{{ t_id }}/toggle_admin" method="POST" class="m-0">
                                            <button class="btn btn-sm {% if t_info.is_admin %}btn-warning{% else %}btn-dark{% endif %} text-nowrap fw-bold shadow-sm">{% if t_info.is_admin %}Revoke Admin{% else %}Make Admin{% endif %}</button>
                                        </form>
                                        <form action="/admin/action_teacher/{{ t_id }}/remove" method="POST" class="m-0" onsubmit="return confirm('Remove this teacher?');">
                                            <button class="btn btn-sm btn-danger shadow-sm"><i class="fa-solid fa-user-xmark"></i></button>
                                        </form>
                                        </div>
                                    {% else %}
                                        <span class="badge border border-dark text-dark px-3 py-2">You</span>
                                    {% endif %}
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table></div>
                </div>
            </div>
        </div>
    </div>

    <div class="tab-pane fade" id="settings">
        <div class="row">
            <div class="col-lg-6 mb-4">
                <div class="card p-4 border-top border-4 border-dark">
                    <h5 class="fw-bold mb-3">My Admin Account</h5>
                    <form action="/admin/change_password" method="POST" autocomplete="off">
                        <input type="password" name="new_password" class="form-control mb-3" required placeholder="New Password" minlength="6" autocomplete="new-password">
                        <button class="btn btn-dark w-100 mb-4 fw-bold shadow-sm">Update My Password</button>
                    </form>
                    <form action="/admin/resign" method="POST" onsubmit="return confirm('Are you sure you want to step down as Admin?');">
                        <button class="btn btn-outline-warning text-dark w-100 fw-bold">Resign from Admin Role</button>
                    </form>
                </div>
            </div>
            <div class="col-lg-6 mb-4">
                <div class="card p-4 border border-danger bg-light">
                    <h5 class="fw-bold text-danger mb-2"><i class="fa-solid fa-triangle-exclamation me-2"></i>Danger Zone</h5>
                    <p class="text-muted small mb-4">Deleting the school removes ALL classes, teachers, and messages permanently.</p>
                    <form action="/admin/delete_school" method="POST">
                        <label class="small fw-bold mb-2 text-dark">Type <code class="fs-6 px-2 py-1 bg-white border rounded text-danger">confirm delete {{ school.name }}</code> below:</label>
                        <input type="text" name="confirm_text" class="form-control border-danger mb-3" required autocomplete="off">
                        <button class="btn btn-danger w-100 fw-bold shadow-sm"><i class="fa-solid fa-radiation me-2"></i>Permanently Delete School</button>
                    </form>
                </div>
            </div>
        </div>
    </div>
</div>
""")

STUDENT_AUTH_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="row justify-content-center px-2"><div class="col-md-7 col-lg-5"><div class="card p-4 p-md-5 border-top border-4 border-primary">
    <div class="text-center mb-4">
        <i class="fa-solid fa-user-graduate fa-3x text-primary mb-3"></i>
        <h3 class="fw-bold">Student Access</h3>
    </div>
    <form action="/student_auth" method="POST">
        <div class="form-floating mb-3">
            <select name="school_id" class="form-select fw-bold text-primary" id="selSchool" required onchange="this.form.submit()">
                <option value="" disabled {% if not selected_school %}selected{% endif %}>1. Choose your school</option>
                {% for s_id, s_info in schools.items() %}<option value="{{ s_id }}" {% if selected_school == s_id %}selected{% endif %}>{{ s_info.name }}</option>{% endfor %}
            </select>
            <label for="selSchool">School</label>
        </div>
    </form>
    {% if selected_school %}
    <form action="/student_login_class" method="POST" class="pt-3 border-top mt-3" autocomplete="off">
        <input type="hidden" name="school_id" value="{{ selected_school }}">
        <div class="form-floating mb-3">
            <select name="class_id" class="form-select" id="selClass" required>
                <option value="" disabled selected>2. Choose your class</option>
                {% for c_id, c_info in classes.items() %}<option value="{{ c_id }}">{{ c_info.name }}</option>{% endfor %}
            </select>
            <label for="selClass">Class</label>
        </div>
        <div class="form-floating mb-4">
            <input type="password" name="password" class="form-control" id="classPw" placeholder="3. Class Password" required autocomplete="new-password">
            <label for="classPw">Class Password</label>
        </div>
        <button type="submit" class="btn btn-primary w-100 btn-lg fw-bold shadow-sm">Enter Class Portal</button>
    </form>
    {% endif %}
</div></div></div>
""")

STUDENT_PORTAL_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="row px-2">
    <div class="col-lg-4 mb-4">
        <div class="card p-4 shadow-sm border-top border-4 border-primary sticky-lg-top" style="top: 20px;">
            <h4 class="fw-bold mb-4"><i class="fa-solid fa-paper-plane text-primary me-2"></i>Send Message</h4>
            <form action="/send_message" method="POST">
                <div class="form-floating mb-3">
                    <select name="teacher_id" class="form-select fw-bold" id="selTeacher" required>
                        <option value="" disabled selected>Select Teacher</option>
                        {% for t_id, t_info in teachers.items() %}<option value="{{ t_id }}">{{ t_info.subject }} ({{ t_info.username }})</option>{% endfor %}
                    </select>
                    <label for="selTeacher">To</label>
                </div>
                <div class="form-floating mb-4">
                    <textarea name="text" class="form-control" id="msgText" style="height: 120px" required placeholder="Type your question here..."></textarea>
                    <label for="msgText">Message</label>
                </div>
                <button type="submit" class="btn btn-primary w-100 btn-lg fw-bold shadow-sm">Send Message</button>
            </form>
        </div>
    </div>
    <div class="col-lg-8">
        <div class="card p-3 p-md-4 shadow-sm bg-light border-0">
            <div class="d-flex justify-content-between align-items-center mb-3">
                <h4 class="fw-bold m-0"><i class="fa-solid fa-clock-rotate-left text-secondary me-2"></i>My Recent Messages</h4>
            </div>
            <p class="small text-muted mb-4"><i class="fa-solid fa-circle-info"></i> Messages can be deleted within 5 minutes.</p>
            
            {% for msg in my_messages %}
            <div class="msg-box d-flex flex-column flex-md-row justify-content-between align-items-start">
                <div class="w-100 pe-0 pe-md-3 mb-3 mb-md-0">
                    <div class="d-flex justify-content-between align-items-center mb-2">
                        <span class="badge bg-secondary px-2 py-1 fs-6">To: {{ teachers[msg.teacher_id].subject }}</span>
                        <small class="text-muted fw-bold local-time" data-iso="{{ msg.iso_time }}">{{ msg.timestamp }}</small>
                    </div>
                    <p class="msg-text">{{ msg.text }}</p>
                </div>
                {% if msg.can_delete %}
                <div class="flex-shrink-0 msg-actions">
                    <form action="/student/delete_msg/{{ msg.id }}" method="POST" class="m-0">
                        <button class="btn btn-sm btn-outline-danger fw-bold btn-mobile-full"><i class="fa-solid fa-trash me-1"></i>Delete</button>
                    </form>
                </div>
                {% endif %}
            </div>
            {% else %}
            <div class="text-center py-5 bg-white rounded-4 border"><i class="fa-regular fa-paper-plane fa-3x text-light mb-3"></i><p class="text-muted fs-5 m-0">You haven't sent any messages yet.</p></div>
            {% endfor %}
            <script>setTimeout(() => { window.location.reload(); }, 30000);</script>
        </div>
    </div>
</div>
""")

TEACHER_DASH_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="d-flex flex-column flex-md-row justify-content-between align-items-start align-items-md-center mb-4 bg-white p-4 rounded-4 shadow-sm border-start border-4 border-success gap-3">
    <div>
        <h2 class="fw-bold mb-1 fs-3">Welcome, {{ teacher.username }}</h2>
        <span class="badge bg-dark fs-6 px-3 py-2"><i class="fa-regular fa-calendar me-2"></i>Session: {{ active_day }}</span>
    </div>
    <form action="/new_day" method="POST" class="w-100 w-md-auto m-0" style="max-width: fit-content;">
        <button type="submit" class="btn btn-success fw-bold shadow-sm w-100 px-4"><i class="fa-solid fa-broom me-2"></i>Start Clean Session</button>
    </form>
</div>

<div class="card p-3 p-md-4 mb-4 shadow-sm border border-warning">
    <h5 class="fw-bold text-warning mb-3"><i class="fa-solid fa-star me-2"></i>Pin Note to Legend Board</h5>
    <form action="/teacher/post_legend" method="POST" class="d-flex flex-column flex-md-row gap-2" autocomplete="off">
        <input type="text" name="text" class="form-control form-control-lg flex-grow-1" placeholder="Write a custom announcement or comment..." required>
        <button type="submit" class="btn btn-warning fw-bold text-dark btn-lg shadow-sm text-nowrap"><i class="fa-solid fa-thumbtack me-2"></i>Pin Note</button>
    </form>
</div>

<ul class="nav nav-tabs mb-4 border-0 flex-nowrap overflow-auto hide-scrollbar" id="teacherTabs" role="tablist">
  <li class="nav-item"><button class="nav-link active fw-bold fs-5 btn text-nowrap me-2" data-bs-toggle="tab" data-bs-target="#current">Current Session</button></li>
  <li class="nav-item"><button class="nav-link fw-bold fs-5 btn text-nowrap" data-bs-toggle="tab" data-bs-target="#history">Message History</button></li>
</ul>

<div class="tab-content">
    <div class="tab-pane fade show active" id="current">
        {% for msg in messages %}
        <div class="msg-box {% if msg.is_top %}msg-top{% endif %} d-flex flex-column flex-md-row justify-content-between align-items-start shadow-sm border-0">
            <div class="w-100 pe-0 pe-md-4 mb-3 mb-md-0">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    {% set c_name = classes[msg.class_id].name if msg.class_id in classes else "Teacher Note" %}
                    <span class="badge bg-primary px-2 py-1 fs-6">{{ c_name }}</span>
                    <small class="text-muted fw-bold local-time" data-iso="{{ msg.iso_time }}">{{ msg.timestamp }}</small>
                </div>
                <p class="msg-text">{{ msg.text }}</p>
            </div>
            <div class="d-flex flex-row flex-md-column gap-2 flex-shrink-0 msg-actions">
                {% if not msg.is_top %}
                <form action="/action/top/{{ msg.id }}" method="POST" class="m-0 flex-fill"><button class="btn btn-sm btn-warning w-100 fw-bold shadow-sm">Pin</button></form>
                {% else %}<div class="badge bg-warning text-dark p-2 text-center flex-fill fs-6 shadow-sm"><i class="fa-solid fa-star me-1"></i> Pinned</div>{% endif %}
                <form action="/action/delete/{{ msg.id }}" method="POST" class="m-0 flex-fill"><button class="btn btn-sm btn-outline-danger w-100 fw-bold bg-white">Delete</button></form>
            </div>
        </div>
        {% else %}
        <div class="text-center py-5 bg-white rounded-4 shadow-sm border"><i class="fa-regular fa-comments fa-3x text-light mb-3"></i><h4 class="text-muted m-0">No messages in this session.</h4></div>
        {% endfor %}
    </div>
    
    <div class="tab-pane fade" id="history">
        <div class="alert alert-info border-0 shadow-sm fw-bold"><i class="fa-solid fa-circle-info me-2"></i>This tab shows all messages sent to you across every session.</div>
        {% for msg in all_messages %}
        <div class="msg-box d-flex flex-column flex-md-row justify-content-between align-items-start bg-light border-secondary">
            <div class="w-100 pe-0 pe-md-4 mb-3 mb-md-0">
                <div class="d-flex justify-content-between align-items-center mb-2">
                    {% set c_name = classes[msg.class_id].name if msg.class_id in classes else "Teacher Note" %}
                    <span class="badge bg-secondary px-2 py-1">{{ c_name }} | {{ msg.day_id }}</span>
                    <small class="text-muted fw-bold local-time" data-iso="{{ msg.iso_time }}">{{ msg.timestamp }}</small>
                </div>
                <p class="msg-text">{{ msg.text }}</p>
            </div>
            <div class="flex-shrink-0 msg-actions">
                <form action="/action/delete/{{ msg.id }}" method="POST" class="m-0"><button class="btn btn-sm btn-outline-danger fw-bold w-100 bg-white">Delete</button></form>
            </div>
        </div>
        {% else %}
        <div class="text-center py-5 bg-white rounded-4 shadow-sm border"><h4 class="text-muted m-0">No message history found.</h4></div>
        {% endfor %}
    </div>
</div>
""")

LEGEND_HTML = BASE_HTML.replace('{% block content %}{% endblock %}', """
<div class="d-flex flex-column flex-md-row justify-content-between align-items-start align-items-md-center mb-4 gap-3 px-2">
    <h2 class="fw-bold text-dark m-0 fs-2"><i class="fa-solid fa-trophy text-warning me-3"></i>Legend Board</h2>
    {% if session.get('teacher_id') %}
    <form action="/clear_legend" method="POST" class="m-0 w-100 w-md-auto" onsubmit="return confirm('Remove ALL pinned messages from the Legend Board?');">
        <button class="btn btn-danger w-100 fw-bold shadow-sm px-4"><i class="fa-solid fa-eraser me-2"></i>Clear Board</button>
    </form>
    {% endif %}
</div>
<div class="row px-2">
    {% for msg in messages %}
    <div class="col-md-6 col-lg-4 mb-4">
        <div class="card h-100 border-top border-4 border-warning shadow-sm">
            <div class="card-header bg-white d-flex justify-content-between align-items-center flex-wrap gap-2 py-3 border-0">
                <span class="fw-bold text-dark fs-5">{{ schools[msg.school_id].name }}</span>
                <span class="badge bg-warning text-dark px-2 py-1 fs-6">{{ teachers[msg.teacher_id].subject }}</span>
            </div>
            <div class="card-body bg-light rounded-bottom-4 d-flex flex-column">
                {% set c_name = classes[msg.class_id].name if msg.class_id in classes else "Teacher's Note" %}
                <span class="badge bg-primary align-self-start mb-3 px-2 py-1">{{ c_name }}</span>
                <p class="fs-4 fw-bold text-dark msg-text flex-grow-1" style="line-height: 1.4;">"{{ msg.text }}"</p>
                <div class="text-end mt-3 border-top pt-2">
                    <small class="text-muted fw-bold local-time" data-iso="{{ msg.iso_time }}">{{ msg.timestamp }}</small>
                </div>
            </div>
        </div>
    </div>
    {% else %}<div class="col-12 text-center py-5 bg-white rounded-4 shadow-sm border"><p class="fs-4 text-muted m-0"><i class="fa-regular fa-star mb-3 d-block fa-2x"></i>No top comments pinned yet.</p></div>{% endfor %}
</div>
""")

# --- ROUTES ---

@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

@app.route('/create_school', methods=['GET', 'POST'])
def create_school():
    if request.method == 'POST':
        s_name = request.form['school_name'].strip()
        if any(s['name'].lower() == s_name.lower() for s in db["schools"].values()):
            flash("School name already taken!", "danger")
            return redirect(url_for('create_school'))

        s_id = str(uuid.uuid4())[:8]
        t_id = str(uuid.uuid4())[:8]
        hashed_pw = generate_password_hash(request.form['password'])
        
        db["schools"][s_id] = {"name": s_name, "created_by": t_id}
        db["teachers"][t_id] = {"school_id": s_id, "username": request.form['username'], "password_hash": hashed_pw, "subject": request.form['subject'], "is_admin": True, "is_approved": True}
        db["active_days"][t_id] = "Day-1"
        
        save_schools()
        save_teachers()
        
        flash("School and Admin account created! Please log in.", "success")
        return redirect(url_for('teacher_login'))
    return render_template_string(CREATE_SCHOOL_HTML)

@app.route('/teacher_login', methods=['GET', 'POST'])
def teacher_login():
    if request.method == 'POST':
        load_data(force=True) 
        for tid, tinfo in db["teachers"].items():
            if tinfo["school_id"] == request.form['school_id'] and tinfo["username"] == request.form['username']:
                if check_password_hash(tinfo["password_hash"], request.form['password']):
                    if not tinfo["is_approved"]:
                        flash("Account Pending: An Admin must approve you before you can log in.", "warning")
                        return redirect(url_for('teacher_login'))
                    
                    session.permanent = True
                    session['teacher_id'] = tid
                    session['school_id'] = tinfo["school_id"]
                    session['is_admin'] = tinfo["is_admin"]
                    return redirect(url_for('teacher_dashboard'))
        flash("Invalid Credentials", "danger")
    return render_template_string(TEACHER_LOGIN_HTML, schools=db["schools"])

@app.route('/teacher_register', methods=['POST'])
def teacher_register():
    s_id = request.form['school_id']
    subj = request.form['subject'].strip()
    if any(t['school_id'] == s_id and t['subject'].lower() == subj.lower() for t in db["teachers"].values()):
        flash(f"A teacher for '{subj}' already exists here!", "danger")
        return redirect(url_for('teacher_login'))

    t_id = str(uuid.uuid4())[:8]
    db["teachers"][t_id] = {
        "school_id": s_id, "username": request.form['username'], 
        "password_hash": generate_password_hash(request.form['password']), 
        "subject": subj, "is_admin": False, "is_approved": False
    }
    save_teachers()
    flash("Registered! Please wait for an Admin to approve your account.", "success")
    return redirect(url_for('teacher_login'))

@app.route('/admin_dashboard')
def admin_dashboard():
    if not session.get('is_admin'): return redirect(url_for('index'))
    s_id = session.get('school_id')
    school = db["schools"].get(s_id)
    if not school:
        session.clear()
        return redirect(url_for('index'))

    c_list = {cid: info for cid, info in db["classes"].items() if info["school_id"] == s_id}
    p_teachers = {tid: info for tid, info in db["teachers"].items() if info["school_id"] == s_id and not info["is_approved"]}
    a_teachers = {tid: info for tid, info in db["teachers"].items() if info["school_id"] == s_id and info["is_approved"]}
    return render_template_string(ADMIN_DASH_HTML, school=school, classes=c_list, pending_teachers=p_teachers, approved_teachers=a_teachers)

@app.route('/admin/add_class', methods=['POST'])
def admin_add_class():
    if not session.get('is_admin'): return redirect(url_for('index'))
    c_id = str(uuid.uuid4())[:8]
    db["classes"][c_id] = {"school_id": session.get('school_id'), "name": request.form['class_name'], "password_hash": generate_password_hash(request.form['class_password'])}
    save_classes()
    flash("Class created!", "success")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/change_class_pw/<c_id>', methods=['POST'])
def admin_change_class_pw(c_id):
    if session.get('is_admin') and c_id in db["classes"] and db["classes"][c_id]["school_id"] == session.get('school_id'):
        db["classes"][c_id]["password_hash"] = generate_password_hash(request.form['new_password'])
        save_classes()
        flash("Class password successfully updated.", "success")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete_class/<c_id>', methods=['POST'])
def admin_delete_class(c_id):
    if session.get('is_admin') and c_id in db["classes"] and db["classes"][c_id]["school_id"] == session.get('school_id'):
        del db["classes"][c_id]
        db["messages"] = [m for m in db["messages"] if m["class_id"] != c_id] 
        save_classes()
        save_messages()
        flash("Class deleted.", "success")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/action_teacher/<t_id>/<action>', methods=['POST'])
def admin_action_teacher(t_id, action):
    if not session.get('is_admin') or t_id not in db["teachers"]: return redirect(url_for('admin_dashboard'))
    
    if db["teachers"][t_id]["school_id"] == session.get('school_id'):
        if action == "approve": 
            db["teachers"][t_id]["is_approved"] = True
            db["active_days"][t_id] = "Day-1"
            flash("Teacher Approved!", "success")
        elif action == "reject" or action == "remove":
            del db["teachers"][t_id]
            db["messages"] = [m for m in db["messages"] if m["teacher_id"] != t_id]
            flash("Teacher Removed/Rejected.", "success")
        elif action == "toggle_admin":
            db["teachers"][t_id]["is_admin"] = not db["teachers"][t_id]["is_admin"]
            flash("Admin role updated.", "success")
        save_teachers()
        save_messages()
            
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/change_password', methods=['POST'])
def admin_change_pw():
    if session.get('is_admin'):
        db["teachers"][session['teacher_id']]["password_hash"] = generate_password_hash(request.form['new_password'])
        save_teachers()
        flash("Admin Password successfully updated.", "success")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/resign', methods=['POST'])
def admin_resign():
    if session.get('is_admin'):
        s_id = session.get('school_id')
        admins = [t for t in db["teachers"].values() if t["school_id"] == s_id and t["is_admin"]]
        if len(admins) > 1:
            db["teachers"][session['teacher_id']]["is_admin"] = False
            session['is_admin'] = False
            save_teachers()
            flash("You have resigned from Admin. You are now a regular teacher.", "info")
            return redirect(url_for('teacher_dashboard'))
        else:
            flash("You are the ONLY admin! Make someone else an admin before resigning.", "danger")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete_school', methods=['POST'])
def admin_delete_school():
    if not session.get('is_admin'): return redirect(url_for('index'))
    s_id = session.get('school_id')
    school_name = db["schools"][s_id]["name"]
    expected_text = f"confirm delete {school_name}"
    
    if request.form['confirm_text'] == expected_text:
        del db["schools"][s_id]
        db["classes"] = {k: v for k, v in db["classes"].items() if v["school_id"] != s_id}
        db["teachers"] = {k: v for k, v in db["teachers"].items() if v["school_id"] != s_id}
        db["messages"] = [m for m in db["messages"] if m["school_id"] != s_id]
        save_schools()
        save_classes()
        save_teachers()
        save_messages()
        session.clear()
        flash("School entirely deleted.", "success")
        return redirect(url_for('index'))
    else:
        flash("Confirmation text did not match exactly. Deletion cancelled.", "danger")
        return redirect(url_for('admin_dashboard'))

@app.route('/student_auth', methods=['GET', 'POST'])
def student_auth():
    sel_school = request.form.get('school_id') if request.method == 'POST' else None
    c_list = {cid: info for cid, info in db["classes"].items() if info["school_id"] == sel_school} if sel_school else {}
    return render_template_string(STUDENT_AUTH_HTML, schools=db["schools"], selected_school=sel_school, classes=c_list)

@app.route('/student_login_class', methods=['POST'])
def student_login_class():
    load_data(force=True) 
    c_info = db["classes"].get(request.form['class_id'])
    if c_info and check_password_hash(c_info["password_hash"], request.form['password']):
        session.permanent = True
        session['student_class_id'] = request.form['class_id']
        session['student_school_id'] = request.form['school_id']
        session['student_session_id'] = str(uuid.uuid4())
        return redirect(url_for('student_portal'))
    flash("Incorrect Class Password!", "danger")
    return redirect(url_for('student_auth'))

@app.route('/student_portal')
def student_portal():
    s_id = session.get('student_school_id')
    if not s_id: return redirect(url_for('student_auth'))
    t_list = {tid: tinfo for tid, tinfo in db["teachers"].items() if tinfo["school_id"] == s_id and tinfo["is_approved"]}
    
    my_msgs = []
    for m in db["messages"]:
        if m.get('student_id') == session.get('student_session_id'):
            time_diff = (datetime.utcnow() - datetime.fromisoformat(m['iso_time'])).total_seconds()
            m_copy = m.copy()
            m_copy['can_delete'] = time_diff <= 300 
            my_msgs.append(m_copy)
    
    my_msgs.reverse() 
    return render_template_string(STUDENT_PORTAL_HTML, teachers=t_list, my_messages=my_msgs)

@app.route('/send_message', methods=['POST'])
def send_message():
    s_id = session.get('student_school_id')
    c_id = session.get('student_class_id')
    t_id = request.form['teacher_id']
    if not s_id: return redirect(url_for('student_auth'))
    
    # Absolute UTC Time logged via Python
    now = datetime.utcnow()
    python_timestamp = now.strftime("%b %d, %Y - %I:%M %p UTC")
    
    msg = {
        "id": str(uuid.uuid4())[:8], "school_id": s_id, "class_id": c_id, "teacher_id": t_id,
        "student_id": session.get('student_session_id'), "text": request.form['text'], "is_top": False,
        "day_id": db["active_days"].get(t_id, "Day-1"), "timestamp": python_timestamp, "iso_time": now.isoformat()
    }
    
    db["messages"].append(msg)
    # ATOMIC APPEND: Instantly adds to sheet without overriding concurrent users
    append_sheet_row('Messages', [msg['id'], msg['school_id'], msg['class_id'], msg['teacher_id'], msg['student_id'], msg['text'], str(msg['is_top']), msg['day_id'], msg['timestamp'], msg['iso_time']])
    
    flash("Message sent! You have 5 minutes to delete it if needed.", "success")
    return redirect(url_for('student_portal'))

@app.route('/student/delete_msg/<m_id>', methods=['POST'])
def student_delete_msg(m_id):
    s_ses = session.get('student_session_id')
    for m in db["messages"]:
        if m['id'] == m_id and m.get('student_id') == s_ses:
            if (datetime.utcnow() - datetime.fromisoformat(m['iso_time'])).total_seconds() <= 300:
                db["messages"].remove(m)
                save_messages()
                flash("Message successfully deleted.", "success")
            else:
                flash("Time limit exceeded. Message cannot be deleted anymore.", "danger")
            break
    return redirect(url_for('student_portal'))

@app.route('/teacher_dashboard')
def teacher_dashboard():
    tid = session.get('teacher_id')
    if not tid or tid not in db["teachers"]: 
        session.clear()
        return redirect(url_for('teacher_login'))
    
    teacher = db["teachers"][tid]
    active_day = db["active_days"].get(tid, "Day-1")
    
    active_msgs = [m for m in db["messages"] if m['teacher_id'] == tid and m['day_id'] == active_day]
    all_msgs = [m for m in db["messages"] if m['teacher_id'] == tid]
    all_msgs.reverse() 
    
    return render_template_string(TEACHER_DASH_HTML, teacher=teacher, messages=active_msgs, all_messages=all_msgs, active_day=active_day, classes=db["classes"])

@app.route('/new_day', methods=['POST'])
def new_day():
    if session.get('teacher_id'):
        db["active_days"][session['teacher_id']] = "Session-" + datetime.utcnow().strftime("%b%d-%H%M")
        save_teachers()
        flash("Started a brand new session!", "success")
    return redirect(url_for('teacher_dashboard'))

@app.route('/teacher/post_legend', methods=['POST'])
def teacher_post_legend():
    tid = session.get('teacher_id')
    if not tid: return redirect(url_for('teacher_login'))
    
    now = datetime.utcnow()
    python_timestamp = now.strftime("%b %d, %Y - %I:%M %p UTC")
    
    msg = {
        "id": str(uuid.uuid4())[:8], "school_id": db["teachers"][tid]["school_id"], 
        "class_id": "TEACHER_NOTE", "teacher_id": tid, "student_id": "TEACHER", 
        "text": request.form['text'], "is_top": True, "day_id": "Legend", 
        "timestamp": python_timestamp, "iso_time": now.isoformat()
    }
    
    db["messages"].append(msg)
    append_sheet_row('Messages', [msg['id'], msg['school_id'], msg['class_id'], msg['teacher_id'], msg['student_id'], msg['text'], str(msg['is_top']), msg['day_id'], msg['timestamp'], msg['iso_time']])
    
    flash("Note posted directly to Legend Board!", "success")
    return redirect(url_for('teacher_dashboard'))

@app.route('/action/<action_type>/<msg_id>', methods=['POST'])
def message_action(action_type, msg_id):
    tid = session.get('teacher_id')
    if not tid: return redirect(url_for('teacher_login'))
    for msg in db["messages"]:
        if msg['id'] == msg_id and msg['teacher_id'] == tid:
            if action_type == 'top': 
                msg['is_top'] = True
                flash("Message Pinned to Legend!", "success")
            elif action_type == 'delete': 
                db["messages"].remove(msg)
                flash("Message Deleted", "success")
            save_messages()
            break
    return redirect(url_for('teacher_dashboard'))

@app.route('/clear_legend', methods=['POST'])
def clear_legend():
    if not session.get('teacher_id'): return redirect(url_for('index'))
    s_id = session.get('school_id')
    
    for m in db["messages"]:
        if m['school_id'] == s_id and m['is_top']:
            m['is_top'] = False 
            
    save_messages()
    flash("Legend Board has been successfully cleared.", "success")
    return redirect(url_for('legend'))

@app.route('/legend')
def legend():
    top_msgs = [m for m in db["messages"] if m['is_top']]
    top_msgs.reverse()
    return render_template_string(LEGEND_HTML, messages=top_msgs, schools=db["schools"], classes=db["classes"], teachers=db["teachers"])

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run()
