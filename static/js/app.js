/**
 * Complaint Management System - Core SPA Client Logic
 */

let currentUser = null;
let currentDepartments = [];
let allStaffUsers = [];
let allComplaintsCache = [];
let activeComplaintId = null;
let driveDiagnostic = null;
let allRolesCache = [];
let allPermissionsCache = [];
let auditTrailCache = [];
let auditSearchDebounceTimer = null;

function hasPermission(permissionId) {
    if (!currentUser) return false;
    const active = String(currentUser.is_active || 'TRUE').toUpperCase();
    if (active !== 'TRUE' && active !== '1' && active !== 'YES') return false;
    if (currentUser.role === 'Super Admin') return true;
    const perms = currentUser.permissions || [];
    if (perms.includes('*') || perms.includes(permissionId)) return true;
    return false;
}

document.addEventListener('DOMContentLoaded', () => {
    initApp();
});

async function initApp() {
    setupEventListeners();
    checkUrlAuthParams();
    await checkAuth();
    if (currentUser) {
        await loadDepartments();
        await loadMasterOptions();
        await checkSystemStatus();
        await loadDriveStatus();
        handleHashRouting();
    }
}

function checkUrlAuthParams() {
    let search = window.location.search;
    if (!search && window.location.hash.includes('?')) {
        search = window.location.hash.substring(window.location.hash.indexOf('?'));
    }
    if (!search) return;

    const params = new URLSearchParams(search);
    if (params.get('drive_auth') === 'success') {
        const acc = params.get('account') || 'Google Account';
        if (params.get('verified') === 'true') {
            showToast(`Google Drive connected and live binary test verified for ${acc}!`, 'success');
        } else {
            showToast(`Google Drive OAuth authorized for ${acc}. Run 'Test Live Upload' to verify folder write access.`, 'info');
        }
        cleanUrlParams();
    } else if (params.get('drive_auth') === 'authorized') {
        const acc = params.get('account') || 'Google Account';
        const err = params.get('verify_err');
        showToast(`Google Drive authorized for ${acc}. Live probe pending: ${err || 'Click Test Live Upload to verify.'}`, 'warning');
        cleanUrlParams();
    } else if (params.get('drive_auth') === 'error') {
        const msg = params.get('msg') || 'Unknown OAuth error.';
        showToast(`Google Drive authorization failed: ${msg}`, 'error');
        cleanUrlParams();
    }
}

function cleanUrlParams() {
    try {
        const cleanHash = window.location.hash.split('?')[0] || '#settings';
        const cleanUrl = window.location.pathname + cleanHash;
        window.history.replaceState({}, document.title, cleanUrl);
    } catch (e) {}
}

// =============================================================================
// ROUTING & NAVIGATION
// =============================================================================

function setupEventListeners() {
    // Auth Toggles
    const toggleReg = document.getElementById('toggle-register-link');
    const toggleLog = document.getElementById('toggle-login-link');
    if (toggleReg) {
        toggleReg.addEventListener('click', (e) => {
            e.preventDefault();
            document.getElementById('login-form').style.display = 'none';
            document.getElementById('register-form').style.display = 'block';
            document.getElementById('auth-subtitle').textContent = 'Create an account to lodge and track complaints';
        });
    }
    if (toggleLog) {
        toggleLog.addEventListener('click', (e) => {
            e.preventDefault();
            document.getElementById('register-form').style.display = 'none';
            document.getElementById('login-form').style.display = 'block';
            document.getElementById('auth-subtitle').textContent = 'Sign in to lodge or track complaints';
        });
    }

    // Auth Forms
    const setupAdminForm = document.getElementById('setup-admin-form');
    if (setupAdminForm) setupAdminForm.addEventListener('submit', handleSetupAdmin);
    document.getElementById('login-form').addEventListener('submit', handleLogin);
    document.getElementById('register-form').addEventListener('submit', handleRegister);
    document.getElementById('logout-btn').addEventListener('click', handleLogout);

    // Sidebar Navigation
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const view = item.getAttribute('data-view');
            switchView(view);
        });
    });

    // Hash change router
    window.addEventListener('hashchange', handleHashRouting);

    // Lodge Complaint Form
    const lodgeForm = document.getElementById('lodge-complaint-form');
    if (lodgeForm) lodgeForm.addEventListener('submit', handleLodgeComplaint);

    // Filters & Search
    const mySearch = document.getElementById('my-search-input');
    const myFilter = document.getElementById('my-status-filter');
    const myPrio = document.getElementById('my-priority-filter');
    if (mySearch) mySearch.addEventListener('input', filterMyComplaints);
    if (myFilter) myFilter.addEventListener('change', filterMyComplaints);
    if (myPrio) myPrio.addEventListener('change', filterMyComplaints);

    const manageSearch = document.getElementById('manage-search-input');
    const manageDept = document.getElementById('manage-dept-filter');
    const managePrio = document.getElementById('manage-priority-filter');
    const manageStatus = document.getElementById('manage-status-filter');
    if (manageSearch) manageSearch.addEventListener('input', filterManageComplaints);
    if (manageDept) manageDept.addEventListener('change', filterManageComplaints);
    if (managePrio) managePrio.addEventListener('change', filterManageComplaints);
    if (manageStatus) manageStatus.addEventListener('change', filterManageComplaints);

    // Auto calculate time taken on Lodge form
    const lodgeDate = document.getElementById('comp-date');
    const lodgeClosing = document.getElementById('comp-closing-date');
    if (lodgeDate) lodgeDate.addEventListener('change', recalculateLodgeTimeTaken);
    if (lodgeClosing) lodgeClosing.addEventListener('change', recalculateLodgeTimeTaken);

    // Close multi-select dropdowns when clicking outside
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.multi-select-container')) {
            closeAllMultiSelects();
        }
        if (!e.target.closest('.searchable-select-wrap')) {
            closeAllSearchableSelects();
        }
    });

    // Backdrop click dismiss for modals
    document.querySelectorAll('.modal-backdrop').forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                if (modal.id === 'edit-user-modal') closeEditUserModal();
                else if (modal.id === 'delete-user-modal') closeDeleteUserModal();
                else if (modal.id === 'create-user-modal') closeCreateUserModal();
                else if (modal.id === 'complaint-detail-modal') closeComplaintModal();
                else if (modal.id === 'edit-complaint-modal') closeEditComplaintModal();
                else if (modal.id === 'delete-confirm-modal') closeDeleteConfirmModal();
                else if (modal.id === 'create-dept-modal') closeCreateDeptModal();
                else if (modal.id === 'edit-dept-modal') closeEditDeptModal();
                else if (modal.id === 'delete-dept-modal') closeDeleteDeptModal();
                else if (modal.id === 'modal-master-complaint-type') closeMasterComplaintTypeModal();
                else if (modal.id === 'modal-master-salesperson') closeMasterSalespersonModal();
                else if (modal.id === 'modal-master-delete') closeMasterDeleteModal();
                else if (modal.id === 'configure-oauth-modal') closeConfigureOAuthModal();
                else if (modal.id === 'printable-report-modal') closePrintableReportModal();
                else if (modal.id === 'modal-role-builder') closeRoleBuilderModal();
                else if (modal.id === 'modal-role-delete') closeRoleDeleteModal();
            }
        });
    });

    // Global keyboard navigation: Esc key to dismiss any active modal
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' || e.key === 'Esc') {
            const editUserModal = document.getElementById('edit-user-modal');
            if (editUserModal && editUserModal.style.display && editUserModal.style.display !== 'none') {
                closeEditUserModal();
                return;
            }
            const deleteUserModal = document.getElementById('delete-user-modal');
            if (deleteUserModal && deleteUserModal.style.display && deleteUserModal.style.display !== 'none') {
                closeDeleteUserModal();
                return;
            }
            const createUserModal = document.getElementById('create-user-modal');
            if (createUserModal && createUserModal.style.display && createUserModal.style.display !== 'none') {
                closeCreateUserModal();
                return;
            }
            const editCompModal = document.getElementById('edit-complaint-modal');
            if (editCompModal && editCompModal.style.display && editCompModal.style.display !== 'none') {
                closeEditComplaintModal();
                return;
            }
            const compModal = document.getElementById('complaint-detail-modal');
            if (compModal && compModal.style.display && compModal.style.display !== 'none') {
                closeComplaintModal();
                return;
            }
            const delModal = document.getElementById('delete-confirm-modal');
            if (delModal && delModal.style.display && delModal.style.display !== 'none') {
                closeDeleteConfirmModal();
                return;
            }
            const editDeptModal = document.getElementById('edit-dept-modal');
            if (editDeptModal && editDeptModal.style.display && editDeptModal.style.display !== 'none') {
                closeEditDeptModal();
                return;
            }
            const createDeptModal = document.getElementById('create-dept-modal');
            if (createDeptModal && createDeptModal.style.display && createDeptModal.style.display !== 'none') {
                closeCreateDeptModal();
                return;
            }
            const delDeptModal = document.getElementById('delete-dept-modal');
            if (delDeptModal && delDeptModal.style.display && delDeptModal.style.display !== 'none') {
                closeDeleteDeptModal();
                return;
            }
            const mCtModal = document.getElementById('modal-master-complaint-type');
            if (mCtModal && mCtModal.style.display && mCtModal.style.display !== 'none') {
                closeMasterComplaintTypeModal();
                return;
            }
            const mSpModal = document.getElementById('modal-master-salesperson');
            if (mSpModal && mSpModal.style.display && mSpModal.style.display !== 'none') {
                closeMasterSalespersonModal();
                return;
            }
            const mDelModal = document.getElementById('modal-master-delete');
            if (mDelModal && mDelModal.style.display && mDelModal.style.display !== 'none') {
                closeMasterDeleteModal();
                return;
            }
            const roleBuilderModal = document.getElementById('modal-role-builder');
            if (roleBuilderModal && roleBuilderModal.style.display && roleBuilderModal.style.display !== 'none') {
                closeRoleBuilderModal();
                return;
            }
            const roleDeleteModal = document.getElementById('modal-role-delete');
            if (roleDeleteModal && roleDeleteModal.style.display && roleDeleteModal.style.display !== 'none') {
                closeRoleDeleteModal();
                return;
            }
        }
    });
}

function handleHashRouting() {
    const hash = window.location.hash.replace('#', '') || 'dashboard';
    switchView(hash);
}

function switchView(viewName) {
    if (currentUser) {
        const canLodge = hasPermission('complaints:create');
        const canReports = hasPermission('reports:view');
        const canManage = hasPermission('complaints:change_status') || hasPermission('complaints:assign');
        const canSettings = hasPermission('users:view') || hasPermission('settings:view') || hasPermission('departments:view');
        const canMaster = hasPermission('master_data:view') || hasPermission('master_data:manage');
        const canRoles = hasPermission('roles:view') || hasPermission('roles:manage');
        const canAudit = hasPermission('audit_logs:view') || hasPermission('audit_logs:export');

        if (viewName === 'lodge' && !canLodge) viewName = 'dashboard';
        if (viewName === 'reports' && !canReports) viewName = 'dashboard';
        if (viewName === 'manage' && !canManage) viewName = 'dashboard';
        if (viewName === 'settings' && !canSettings) viewName = 'dashboard';
        if (viewName === 'master-data' && !canMaster) viewName = 'dashboard';
        if (viewName === 'roles' && !canRoles) viewName = 'dashboard';
        if (viewName === 'audit-trail' && !canAudit) viewName = 'dashboard';
    }
    window.location.hash = viewName;
    document.querySelectorAll('.view-panel').forEach(panel => panel.style.display = 'none');
    document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));

    const targetPanel = document.getElementById(`view-${viewName}`);
    const targetNav = document.querySelector(`.nav-item[data-view="${viewName}"]`);

    if (targetPanel) {
        targetPanel.style.display = 'block';
        if (targetNav) targetNav.classList.add('active');

        // Update topbar title
        const headings = {
            'dashboard': 'Dashboard',
            'lodge': 'Lodge a Complaint',
            'my-complaints': 'My Complaints',
            'reports': 'Reports & Analytics Hub',
            'manage': 'Staff Workspace — Manage Complaints',
            'settings': 'Users & System Settings',
            'master-data': 'Master Data Management',
            'roles': 'Roles & Permissions Builder',
            'audit-trail': 'System Security & Audit Trail'
        };
        document.getElementById('page-heading').textContent = headings[viewName] || 'Complaint System';

        // Set default date today if opening lodge view and field is empty
        if (viewName === 'lodge') {
            const dateEl = document.getElementById('comp-date');
            if (dateEl && !dateEl.value) {
                dateEl.value = new Date().toISOString().split('T')[0];
            }
            const custNameEl = document.getElementById('comp-customer-name');
            if (custNameEl && !custNameEl.value && currentUser) {
                custNameEl.value = currentUser.name || '';
            }
            if (driveDiagnostic) {
                updateUploadNotice(driveDiagnostic.can_upload_binary, driveDiagnostic);
            }
            refreshSearchableDropdowns();
        }

        // Trigger view-specific data refresh
        if (viewName === 'dashboard') loadDashboardData();
        if (viewName === 'my-complaints') loadMyComplaints();
        if (viewName === 'reports') initReportsView();
        if (viewName === 'manage') loadManageComplaints();
        if (viewName === 'settings') loadSettingsData();
        if (viewName === 'master-data') loadMasterData();
        if (viewName === 'roles') loadRolesData();
        if (viewName === 'audit-trail') loadAuditTrailData();
    }
}

// =============================================================================
// AUTHENTICATION
// =============================================================================

async function checkAuth() {
    try {
        const res = await fetch('/api/auth/me');
        if (res.ok) {
            const data = await res.json();
            currentUser = data.user;
            renderUserInterface();
        } else {
            showAuthScreen();
        }
    } catch (e) {
        showAuthScreen();
    }
}

async function showAuthScreen() {
    currentUser = null;
    document.getElementById('auth-container').style.display = 'flex';
    document.getElementById('app-shell').style.display = 'none';

    try {
        const res = await fetch('/api/system/setup-status');
        if (res.ok) {
            const data = await res.json();
            if (data.needs_admin_setup) {
                const setupForm = document.getElementById('setup-admin-form');
                if (setupForm) setupForm.style.display = 'block';
                document.getElementById('login-form').style.display = 'none';
                document.getElementById('register-form').style.display = 'none';
                document.getElementById('auth-subtitle').textContent = 'Initial Super Administrator Setup';
                return;
            }
        }
    } catch (e) {
        console.warn('Could not query setup status:', e);
    }

    const setupForm = document.getElementById('setup-admin-form');
    if (setupForm) setupForm.style.display = 'none';
    document.getElementById('login-form').style.display = 'block';
    document.getElementById('register-form').style.display = 'none';
    document.getElementById('auth-subtitle').textContent = 'Sign in to lodge or track complaints';
}

async function handleSetupAdmin(e) {
    e.preventDefault();
    const fullName = document.getElementById('setup-name').value.trim();
    const email = document.getElementById('setup-email').value.trim();
    const password = document.getElementById('setup-password').value;
    const confirmPassword = document.getElementById('setup-password-confirm').value;
    const btn = document.getElementById('setup-submit-btn');

    if (password !== confirmPassword) {
        showToast('Passwords do not match. Please re-enter.', 'error');
        return;
    }
    if (password.length < 8) {
        showToast('Password must be at least 8 characters long.', 'error');
        return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span>Provisioning Administrator...</span>';

    try {
        const res = await fetch('/api/auth/setup-admin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ full_name: fullName, email, password })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
            currentUser = data.user;
            renderUserInterface();
            await loadDepartments();
            await checkSystemStatus();
            handleHashRouting();
        } else {
            showToast(data.message || 'Admin setup failed.', 'error');
        }
    } catch (err) {
        showToast('Network error during setup: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span>Provision Super Administrator</span>';
    }
}

function renderUserInterface() {
    document.getElementById('auth-container').style.display = 'none';
    document.getElementById('app-shell').style.display = 'flex';

    document.getElementById('user-display-name').textContent = currentUser.name;
    document.getElementById('user-display-role').textContent = currentUser.role;

    // Granular RBAC Nav Visibility
    const isSuperAdmin = currentUser && currentUser.role === 'Super Admin';
    const canLodge = hasPermission('complaints:create');
    const canReports = hasPermission('reports:view');
    const canManageComplaints = hasPermission('complaints:change_status') || hasPermission('complaints:assign');
    const canMaster = hasPermission('master_data:view') || hasPermission('master_data:manage');
    const canUsersOrSettings = hasPermission('users:view') || hasPermission('settings:view') || hasPermission('departments:view');
    const canRoles = hasPermission('roles:view') || hasPermission('roles:manage');
    const canAudit = hasPermission('audit_logs:view') || hasPermission('audit_logs:export');

    const lodgeNav = document.querySelector('.nav-item[data-view="lodge"]');
    if (lodgeNav) lodgeNav.style.display = canLodge ? 'block' : 'none';

    const reportsNav = document.querySelector('.nav-item[data-view="reports"]');
    if (reportsNav) reportsNav.style.display = canReports ? 'block' : 'none';

    document.querySelectorAll('.staff-only').forEach(el => el.style.display = canManageComplaints ? 'block' : 'none');
    document.querySelectorAll('.admin-only').forEach(el => el.style.display = canUsersOrSettings ? 'block' : 'none');
    document.querySelectorAll('.super-admin-only').forEach(el => el.style.display = canRoles ? 'block' : 'none');

    // Individual Nav Links
    const rolesNav = document.querySelector('.nav-item[data-view="roles"]');
    if (rolesNav) rolesNav.style.display = canRoles ? 'block' : 'none';

    const auditNav = document.querySelector('.nav-item[data-view="audit-trail"]');
    if (auditNav) auditNav.style.display = canAudit ? 'block' : 'none';

    const masterNav = document.querySelector('.nav-item[data-view="master-data"]');
    if (masterNav) masterNav.style.display = canMaster ? 'block' : 'none';

    const settingsNav = document.querySelector('.nav-item[data-view="settings"]');
    if (settingsNav) settingsNav.style.display = canUsersOrSettings ? 'block' : 'none';
}

async function handleLogin(e) {
    e.preventDefault();
    const email = document.getElementById('login-email').value.trim();
    const password = document.getElementById('login-password').value;
    const btn = document.getElementById('login-submit-btn');

    btn.disabled = true;
    btn.innerHTML = '<span>Signing in...</span>';

    try {
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
            currentUser = data.user;
            renderUserInterface();
            await loadDepartments();
            await checkSystemStatus();
            switchView('dashboard');
        } else {
            showToast(data.message || 'Login failed', 'error');
        }
    } catch (err) {
        showToast('Network error during login. Please check server.', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span>Sign In</span>';
    }
}

async function handleRegister(e) {
    e.preventDefault();
    const full_name = document.getElementById('reg-name').value.trim();
    const email = document.getElementById('reg-email').value.trim();
    const department = document.getElementById('reg-dept').value;
    const password = document.getElementById('reg-password').value;
    const btn = document.getElementById('reg-submit-btn');

    btn.disabled = true;
    btn.innerHTML = '<span>Creating account...</span>';

    try {
        const res = await fetch('/api/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ full_name, email, department, password })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
            document.getElementById('toggle-login-link').click();
            document.getElementById('login-email').value = email;
            document.getElementById('login-password').focus();
        } else {
            showToast(data.message || 'Registration failed', 'error');
        }
    } catch (err) {
        showToast('Network error during registration.', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span>Create Account</span>';
    }
}

async function handleLogout() {
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
        showToast('Logged out successfully.', 'info');
        showAuthScreen();
    } catch (e) {
        showAuthScreen();
    }
}

// =============================================================================
// SYSTEM STATUS & DEPARTMENTS
// =============================================================================

async function checkSystemStatus(isManual = false) {
    const indicatorText = document.getElementById('system-status-text');
    const dot = document.querySelector('.status-dot');

    try {
        const res = await fetch('/api/system/status');
        const data = await res.json();

        if (res.ok && data.status === 'success') {
            if (data.mode === 'live' && data.drive_folder_accessible) {
                dot.className = 'status-dot online';
                indicatorText.textContent = 'Google Sheets: Live';
            } else if (data.mode === 'live' && !data.drive_folder_accessible) {
                dot.className = 'status-dot warning';
                indicatorText.textContent = 'Drive Folder: Unshared';
            } else {
                dot.className = 'status-dot';
                indicatorText.textContent = 'Mode: Mock Store';
            }

            if (isManual) {
                showToast(`System Status: ${data.database_engine}. Drive folder accessible: ${data.drive_folder_accessible}`, 'info');
            }

            // Render diagnostic card if in settings
            renderDiagnosticDetails(data);
        }
    } catch (e) {
        dot.className = 'status-dot warning';
        indicatorText.textContent = 'API Offline';
    }
}

function renderDiagnosticDetails(data) {
    if (data.lan_url) {
        const lanEl = document.getElementById('settings-lan-url-display');
        if (lanEl) lanEl.textContent = data.lan_url;
        const lanUriEl = document.getElementById('drive-redirect-uri-lan');
        if (lanUriEl) lanUriEl.textContent = `${data.lan_url}/api/drive/oauth2callback`;
    }

    const container = document.getElementById('system-diagnostic-details');
    if (!container) return;

    container.innerHTML = `
        <div class="diag-item">
            <strong>Database Mode</strong>
            <span>${data.mode === 'live' ? '🟢 Live Google Sheets API' : '🟡 In-Memory Mock Store'}</span>
        </div>
        <div class="diag-item">
            <strong>Service Account</strong>
            <span>${data.service_account_email || 'Not Configured'}</span>
        </div>
        <div class="diag-item">
            <strong>Google Drive Folder ID</strong>
            <span><code>${data.target_drive_folder_id}</code></span>
        </div>
        <div class="diag-item">
            <strong>Drive Folder Access</strong>
            <span>${data.drive_folder_accessible ? '🟢 Confirmed (Read/Write)' : '🔴 ' + (data.drive_folder_message || 'Folder not shared with Service Account')}</span>
        </div>
        <div class="diag-item">
            <strong>Drive Auth Mode</strong>
            <span>${data.drive_auth_mode === 'user_oauth' ? '👤 User OAuth (Quota: Personal Drive)' : '🤖 Service Account (Quota: Shared Drives)'}</span>
        </div>
        <div class="diag-item">
            <strong>Drive Storage Type</strong>
            <span>${data.drive_is_shared_drive ? '📁 Google Workspace Shared Drive' : '📁 Personal Google Drive'}</span>
        </div>
        <div class="diag-item">
            <strong>Active Spreadsheet ID</strong>
            <span><code>${data.active_spreadsheet_id || 'Auto-created on demand'}</code></span>
        </div>
        <div class="diag-item">
            <strong>Folder Action Link</strong>
            <span><a href="${data.target_drive_folder_url}" target="_blank" class="attachment-link">Open Folder in Google Drive &rarr;</a></span>
        </div>
        ${(!data.drive_is_shared_drive && data.drive_auth_mode === 'service_account') ? `
        <div class="diag-item" style="grid-column: 1 / -1; background: #fff8e6; padding: 10px 14px; border-radius: 6px; border: 1px solid #ffd591; font-size: 0.85rem; color: #873800;">
            ℹ️ <strong>Storage Quota Architecture:</strong> Folder <code>${data.target_drive_folder_id}</code> is on a Personal Google Drive. Google Cloud Service Accounts have 0 bytes personal Drive storage quota; binary uploads require either a Google Workspace Shared Drive or User OAuth credentials (<code>config/google_user_oauth.json</code>). Direct document & Drive share links are fully supported on the Lodge Complaint form.
        </div>` : ''}
    `;
}

// =============================================================================
// GOOGLE DRIVE & OAUTH 2.0 USER AUTHENTICATION
// =============================================================================

async function loadDriveStatus(isManual = false) {
    try {
        const res = await fetch('/api/drive/status');
        const data = await res.json();
        if (res.ok && data.status === 'success' && data.drive) {
            driveDiagnostic = data.drive;
            renderDriveOAuthPanel(data.drive);
            updateUploadNotice(data.drive.can_upload_binary, data.drive);
            if (isManual) {
                showToast(`Drive Status: ${data.drive.auth_mode}. Can upload: ${data.drive.can_upload_binary}`, 'info');
            }
        }
    } catch (e) {
        console.warn('Error loading Drive status:', e);
    }
}

function renderDriveOAuthPanel(drive) {
    const isTokenExpired = Boolean(drive.token_expired || drive.reconnect_required);

    // Mode badge
    const modeBadge = document.getElementById('drive-auth-mode-badge');
    if (modeBadge) {
        if (isTokenExpired) {
            modeBadge.className = 'badge badge-prio-urgent';
            modeBadge.textContent = '⚠️ Token Expired / Revoked';
        } else if (drive.auth_mode === 'user_oauth') {
            modeBadge.className = 'badge badge-status-resolved';
            modeBadge.textContent = '👤 User OAuth (Personal Quota)';
        } else if (drive.is_shared_drive) {
            modeBadge.className = 'badge badge-status-in-progress';
            modeBadge.textContent = '🤖 Service Account (Shared Drive)';
        } else {
            modeBadge.className = 'badge badge-status-under-review';
            modeBadge.textContent = '🤖 Service Account (Personal Drive)';
        }
    }

    // Binary capability badge
    const binaryBadge = document.getElementById('drive-binary-badge');
    if (binaryBadge) {
        if (isTokenExpired) {
            binaryBadge.className = 'badge badge-prio-urgent';
            binaryBadge.textContent = '⚠️ Uploads Blocked (Reauthorization Required)';
        } else if (drive.can_upload_binary && drive.drive_verified_success) {
            binaryBadge.className = 'badge badge-status-resolved';
            binaryBadge.textContent = '✓ Direct Uploads Verified & Enabled';
        } else if (drive.auth_mode === 'user_oauth') {
            binaryBadge.className = 'badge badge-status-under-review';
            binaryBadge.textContent = '⏳ Pending Verification (Run Test Live Upload)';
        } else {
            binaryBadge.className = 'badge badge-prio-urgent';
            binaryBadge.textContent = '⚠️ Direct Uploads Blocked (0-byte SA Quota)';
        }
    }

    // Status alert banner
    const alertEl = document.getElementById('drive-status-alert');
    if (alertEl) {
        if (isTokenExpired) {
            alertEl.style.background = '#fef2f2';
            alertEl.style.border = '1px solid #f87171';
            alertEl.style.color = '#991b1b';
            alertEl.innerHTML = `<strong>Google Drive Reauthorization Required:</strong> Your Google OAuth session for <b>${escapeHtml(drive.active_account_email || 'hr.ajitzone@gmail.com')}</b> has expired or was revoked (<code>invalid_grant</code>). Direct file uploads are paused until reauthorized.<br><button onclick="startGoogleOAuth()" class="btn btn-primary btn-sm" style="margin-top: 10px;">🔄 Reconnect Google Drive</button>`;
        } else if (drive.can_upload_binary) {
            alertEl.style.background = '#f6ffed';
            alertEl.style.border = '1px solid #b7eb8f';
            alertEl.style.color = '#389e0d';
            alertEl.innerHTML = `<strong>Connected:</strong> ${escapeHtml(drive.message || 'Direct binary uploads and downloads fully operational.')}`;
        } else {
            alertEl.style.background = '#fffbe6';
            alertEl.style.border = '1px solid #ffe58f';
            alertEl.style.color = '#d46b08';
            alertEl.innerHTML = `<strong>Personal Drive Storage Notice:</strong> ${escapeHtml(drive.message || 'Service Accounts have 0 bytes storage quota on Personal Google Drive. Authorize below with your personal Gmail account via OAuth to enable direct binary file uploads.')}`;
        }
    }

    // Active Account Email
    const accEl = document.getElementById('drive-diag-account');
    if (accEl) {
        accEl.textContent = drive.active_account_email || 'Not configured';
    }

    // Mode description
    const modeDescEl = document.getElementById('drive-diag-mode');
    if (modeDescEl) {
        modeDescEl.textContent = drive.auth_mode === 'user_oauth'
            ? 'Google OAuth 2.0 Authorization Code (User Quota)'
            : 'Google Service Account (az-data-management)';
    }

    // Storage Quota Box
    const quotaTextEl = document.getElementById('drive-diag-quota-text');
    const quotaBar = document.getElementById('drive-diag-quota-bar');
    const quotaBox = document.querySelector('.quota-progress-container');
    if (quotaTextEl) {
        if (isTokenExpired) {
            quotaTextEl.textContent = 'Token Expired — Quota inaccessible until re-authorized';
            if (quotaBox) quotaBox.style.display = 'none';
        } else if (drive.storage_quota && drive.storage_quota.limit_bytes) {
            const usageGb = drive.storage_quota.usage_gb || 0;
            const limitGb = drive.storage_quota.limit_gb || 15;
            const pct = Math.min(100, Math.round((usageGb / limitGb) * 100));
            quotaTextEl.textContent = `${usageGb} GB used of ${limitGb} GB (${pct}%)`;
            if (quotaBox && quotaBar) {
                quotaBox.style.display = 'block';
                quotaBar.style.width = `${pct}%`;
                quotaBar.style.backgroundColor = pct > 90 ? '#ef4444' : '#2563eb';
            }
        } else if (drive.auth_mode === 'user_oauth') {
            quotaTextEl.textContent = 'Active (Personal Storage Quota)';
            if (quotaBox) quotaBox.style.display = 'none';
        } else {
            quotaTextEl.textContent = '0 Bytes (Google Service Account in Personal Drive)';
            if (quotaBox) quotaBox.style.display = 'none';
        }
    }

    // Binary upload capability detail
    const uploadStatusEl = document.getElementById('drive-diag-upload-status');
    if (uploadStatusEl) {
        if (isTokenExpired) {
            uploadStatusEl.innerHTML = '<span style="color: #dc2626; font-weight: 600;">🔴 Token Expired (invalid_grant) — Click "Reconnect Google Drive"</span>';
        } else if (drive.can_upload_binary && drive.drive_verified_success) {
            uploadStatusEl.innerHTML = '<span style="color: #059669; font-weight: 600;">🟢 Direct Upload Active & Verified (Saves to Drive Folder)</span>';
        } else if (drive.auth_mode === 'user_oauth') {
            uploadStatusEl.innerHTML = '<span style="color: #d97706; font-weight: 600;">🟡 OAuth Authorized — Pending Live Upload Verification Test</span>';
        } else {
            uploadStatusEl.innerHTML = '<span style="color: #dc2626; font-weight: 600;">🔴 Blocked (Personal Drive 0-byte Service Account Quota)</span>';
        }
    }

    // Target folder display
    const folderEl = document.getElementById('drive-diag-folder');
    if (folderEl) {
        folderEl.innerHTML = `<a href="${drive.folder_url}" target="_blank" class="attachment-link">${escapeHtml(drive.folder_name || 'My work')} (<code>${drive.folder_id}</code>) &rarr;</a>`;
    }

    // Last verified probe
    const verifiedEl = document.getElementById('drive-diag-last-verified');
    if (verifiedEl) {
        if (isTokenExpired) {
            verifiedEl.innerHTML = '<span style="color: #dc2626; font-weight: 600;">⚠️ Token Expired — Reauthorization Required</span>';
        } else if (drive.drive_verified_success) {
            verifiedEl.innerHTML = `<span style="color: #059669; font-weight: 600;">🟢 Verified (${escapeHtml(drive.drive_last_verified_at || 'Recently')})</span>`;
        } else if (drive.auth_mode === 'user_oauth') {
            verifiedEl.innerHTML = '<span style="color: #d97706; font-weight: 600;">⏳ OAuth Authorized — Click "🧪 Test Live Upload" to verify write access</span>';
        } else {
            verifiedEl.innerHTML = '<span class="text-muted">Awaiting OAuth Authorization</span>';
        }
    }

    // Redirect URIs
    const redirectUri = drive.redirect_uri || (window.location.origin + '/api/drive/oauth2callback');
    const redEl = document.getElementById('drive-redirect-uri-display');
    if (redEl) redEl.textContent = redirectUri;
    const modalRedEl = document.getElementById('modal-redirect-uri-display');
    if (modalRedEl) modalRedEl.value = redirectUri;
    const vercelRedEl = document.getElementById('drive-redirect-uri-vercel');
    if (vercelRedEl) vercelRedEl.textContent = 'https://complaint-management-system-sigma-nine.vercel.app/api/drive/oauth2callback';

    // Toggle disconnect and authorize buttons
    const disconnectBtn = document.getElementById('btn-drive-disconnect');
    const authBtn = document.getElementById('btn-drive-authorize');
    if (disconnectBtn) {
        disconnectBtn.style.display = (drive.auth_mode === 'user_oauth' && !isTokenExpired) ? 'inline-block' : 'none';
    }
    if (authBtn) {
        authBtn.textContent = isTokenExpired ? '🔄 Reconnect Google Drive' : (drive.auth_mode === 'user_oauth' ? '🔄 Re-Authorize Google Drive' : '🔑 Authorize with Google');
    }
}

function updateUploadNotice(canUpload, drive = null) {
    const noticeEl = document.getElementById('lodge-upload-quota-notice');
    if (!noticeEl) return;
    if (drive && (drive.token_expired || drive.reconnect_required)) {
        noticeEl.innerHTML = `<span style="color: #dc2626; font-weight: 500;">⚠️ Google Drive authorization has expired. <a href="#settings" style="text-decoration: underline; color: #dc2626; font-weight: 600;">Reconnect Google Drive in Settings</a> to enable direct uploads. Alternatively, paste a share link in the field above.</span>`;
    } else if (canUpload) {
        const email = drive?.active_account_email ? ` (${drive.active_account_email})` : '';
        noticeEl.innerHTML = `<span style="color: #059669; font-weight: 500;">✓ Direct Google Drive upload active${email}. Files save to target folder.</span>`;
    } else {
        noticeEl.innerHTML = `<span style="color: #d97706; font-weight: 500;">⚠️ Personal Google Drive has 0-byte service account storage quota. Direct binary uploads require Google OAuth authorization in Settings. Alternatively, paste a share link in the field above.</span>`;
    }
}

function openConfigureOAuthModal() {
    const modal = document.getElementById('configure-oauth-modal');
    if (!modal) return;
    const redirectUri = driveDiagnostic?.redirect_uri || (window.location.origin + '/api/drive/oauth2callback');
    const modalRedEl = document.getElementById('modal-redirect-uri-display');
    if (modalRedEl) modalRedEl.value = redirectUri;
    document.getElementById('oauth-config-msg').style.display = 'none';
    modal.style.display = 'flex';
}

function closeConfigureOAuthModal() {
    const modal = document.getElementById('configure-oauth-modal');
    if (modal) modal.style.display = 'none';
    syncBodyModalOpen();
}

async function submitConfigureOAuth(e) {
    e.preventDefault();
    const clientId = document.getElementById('oauth-client-id').value.trim();
    const clientSecret = document.getElementById('oauth-client-secret').value.trim();
    const saveBtn = document.getElementById('btn-save-oauth-config');
    const msgEl = document.getElementById('oauth-config-msg');

    if (!clientId || !clientSecret) {
        showToast('Both Client ID and Client Secret are required.', 'error');
        return;
    }

    if (!clientId.includes('googleusercontent.com')) {
        showToast('Client ID should end with .apps.googleusercontent.com', 'error');
        return;
    }

    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving to Google Sheets...';

    try {
        const res = await fetch('/api/drive/configure-client', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ client_id: clientId, client_secret: clientSecret })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || 'OAuth Client configured successfully!', 'success');
            closeConfigureOAuthModal();
            await loadDriveStatus();
            // Automatically prompt the user to authorize
            showToast('Credentials saved! Now click "Authorize with Google" to grant access from your Gmail account.', 'info');
        } else {
            msgEl.style.display = 'block';
            msgEl.style.background = '#fee2e2';
            msgEl.style.color = '#991b1b';
            msgEl.textContent = data.message || 'Failed to save configuration.';
        }
    } catch (err) {
        msgEl.style.display = 'block';
        msgEl.style.background = '#fee2e2';
        msgEl.style.color = '#991b1b';
        msgEl.textContent = 'Network error saving OAuth credentials: ' + err.message;
    } finally {
        saveBtn.disabled = false;
        saveBtn.textContent = 'Save Credentials & Continue';
    }
}

async function startGoogleOAuth() {
    try {
        const res = await fetch('/api/drive/auth-url');
        const data = await res.json();
        if (res.ok && data.auth_url) {
            showToast('Redirecting to Google OAuth Consent...', 'info');
            window.location.href = data.auth_url;
        } else {
            showToast(data.message || 'OAuth Client ID & Secret not configured yet. Please configure them first.', 'error');
            openConfigureOAuthModal();
        }
    } catch (err) {
        showToast('Failed to start OAuth: ' + err.message, 'error');
        openConfigureOAuthModal();
    }
}

async function testDriveConnection() {
    const btn = document.getElementById('btn-drive-test-upload');
    const verifiedEl = document.getElementById('drive-diag-last-verified');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '🧪 Testing Probe...';
    }

    try {
        const res = await fetch('/api/drive/test-upload', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await res.json();
        if (res.ok && data.verified) {
            showToast(data.message || 'Google Drive binary test passed successfully!', 'success');
            if (verifiedEl) {
                verifiedEl.innerHTML = `<span style="color: #059669; font-weight: 600;">🟢 Probe Verified (${new Date().toLocaleTimeString()})</span>`;
            }
            await loadDriveStatus();
        } else {
            showToast(data.message || 'Live Drive upload test failed.', 'error');
            if (verifiedEl) {
                verifiedEl.innerHTML = `<span style="color: #dc2626; font-weight: 600;">🔴 Test Failed: ${escapeHtml(data.message || 'Error')}</span>`;
            }
        }
    } catch (err) {
        showToast('Network error during Drive test: ' + err.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = '🧪 Test Live Upload';
        }
    }
}

async function disconnectGoogleDrive() {
    if (!confirm('Are you sure you want to disconnect Google Drive User OAuth? Direct binary uploads will be blocked until re-authorized.')) {
        return;
    }

    try {
        const res = await fetch('/api/drive/disconnect', { method: 'POST' });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || 'Google Drive OAuth disconnected.', 'info');
            await loadDriveStatus();
        } else {
            showToast(data.message || 'Failed to disconnect.', 'error');
        }
    } catch (err) {
        showToast('Network error disconnecting Drive: ' + err.message, 'error');
    }
}

function copyLanUrl() {
    const el = document.getElementById('settings-lan-url-display');
    const url = (el && el.textContent.trim()) || 'http://192.168.1.55:5050';
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(() => {
            showToast('Office LAN URL copied to clipboard: ' + url, 'success');
        }).catch(() => {
            prompt('Office LAN URL for other laptops on Wi-Fi:', url);
        });
    } else {
        prompt('Office LAN URL for other laptops on Wi-Fi:', url);
    }
}

function copyRedirectUri(elementId) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const text = el.value || el.textContent || '';
    if (!text) return;

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
            showToast('Redirect URI copied to clipboard!', 'info');
        }).catch(() => {
            prompt('Copy this redirect URI:', text);
        });
    } else {
        prompt('Copy this redirect URI:', text);
    }
}

// =============================================================================
// MULTI-SELECT DEPARTMENT DROPDOWN LOGIC
// =============================================================================

function toggleMultiSelect(prefix) {
    const dropdown = document.getElementById(`${prefix}-dept-dropdown`);
    const container = document.getElementById(`${prefix}-dept-multi-select`);
    if (!dropdown || !container) return;

    const isVisible = dropdown.style.display !== 'none';
    closeAllMultiSelects();
    if (!isVisible) {
        dropdown.style.display = 'flex';
        container.classList.add('open');
        const searchInput = document.getElementById(`${prefix}-dept-search`);
        if (searchInput) {
            searchInput.value = '';
            filterMultiSelectOptions(prefix);
            searchInput.focus();
        }
    }
}

function closeAllMultiSelects() {
    ['lodge', 'edit'].forEach(prefix => {
        const dropdown = document.getElementById(`${prefix}-dept-dropdown`);
        const container = document.getElementById(`${prefix}-dept-multi-select`);
        if (dropdown) dropdown.style.display = 'none';
        if (container) container.classList.remove('open');
    });
}

function filterMultiSelectOptions(prefix) {
    const searchVal = (document.getElementById(`${prefix}-dept-search`)?.value || '').toLowerCase().trim();
    const container = document.getElementById(`${prefix}-dept-options`);
    if (!container) return;
    const options = container.querySelectorAll('.multi-select-option');
    options.forEach(opt => {
        const name = (opt.getAttribute('data-name') || '').toLowerCase();
        opt.style.display = (!searchVal || name.includes(searchVal)) ? 'flex' : 'none';
    });
}

function onDeptCheckboxChange(prefix) {
    const container = document.getElementById(`${prefix}-dept-options`);
    let hiddenInput = null;
    if (prefix === 'lodge') hiddenInput = document.getElementById('comp-dept');
    else if (prefix === 'edit') hiddenInput = document.getElementById('edit-dept');
    else if (prefix === 'create-user') hiddenInput = document.getElementById('modal-user-dept');
    else if (prefix === 'edit-user') hiddenInput = document.getElementById('edit-user-dept');
    const toggle = document.getElementById(`${prefix}-dept-toggle`);
    if (!container || !hiddenInput) return;

    const checkedBoxes = Array.from(container.querySelectorAll('input[type="checkbox"]:checked'));
    const selectedVals = checkedBoxes.map(cb => cb.value.trim()).filter(Boolean);

    hiddenInput.value = selectedVals.join(', ');
    if (toggle) toggle.classList.remove('is-invalid');
    updateMultiSelectLabel(prefix, selectedVals);
}

function selectAllDepartments(prefix) {
    const container = document.getElementById(`${prefix}-dept-options`);
    if (!container) return;
    const checkboxes = container.querySelectorAll('.multi-select-option:not([style*="display: none"]) input[type="checkbox"]');
    checkboxes.forEach(cb => { cb.checked = true; });
    onDeptCheckboxChange(prefix);
}

function clearAllDepartments(prefix) {
    const container = document.getElementById(`${prefix}-dept-options`);
    if (!container) return;
    const checkboxes = container.querySelectorAll('input[type="checkbox"]');
    checkboxes.forEach(cb => { cb.checked = false; });
    onDeptCheckboxChange(prefix);
}

function updateMultiSelectLabel(prefix, selectedVals) {
    const labelEl = document.getElementById(`${prefix}-dept-label`);
    if (!labelEl) return;

    if (!selectedVals || selectedVals.length === 0) {
        labelEl.innerHTML = '<span class="text-muted">Select Department(s)...</span>';
    } else if (selectedVals.length === 1) {
        labelEl.innerHTML = `<span class="multi-select-chip">${escapeHtml(selectedVals[0])}</span>`;
    } else if (selectedVals.length <= 2) {
        labelEl.innerHTML = selectedVals.map(v => `<span class="multi-select-chip">${escapeHtml(v)}</span>`).join('');
    } else {
        labelEl.innerHTML = `<span class="multi-select-chip">${escapeHtml(selectedVals[0])}</span> <span class="multi-select-chip">+${selectedVals.length - 1} more</span>`;
    }
}

function setMultiSelectValues(prefix, val) {
    const container = document.getElementById(`${prefix}-dept-options`);
    let hiddenInput = null;
    if (prefix === 'lodge') hiddenInput = document.getElementById('comp-dept');
    else if (prefix === 'edit') hiddenInput = document.getElementById('edit-dept');
    else if (prefix === 'create-user') hiddenInput = document.getElementById('modal-user-dept');
    else if (prefix === 'edit-user') hiddenInput = document.getElementById('edit-user-dept');
    if (!container || !hiddenInput) return;

    let targetArray = [];
    if (Array.isArray(val)) {
        targetArray = val.map(s => String(s).trim());
    } else if (typeof val === 'string') {
        targetArray = val.split(',').map(s => s.trim()).filter(Boolean);
    }

    const checkboxes = container.querySelectorAll('input[type="checkbox"]');
    checkboxes.forEach(cb => {
        cb.checked = targetArray.includes(cb.value.trim());
    });

    const checkedBoxes = Array.from(container.querySelectorAll('input[type="checkbox"]:checked'));
    const selectedVals = checkedBoxes.map(cb => cb.value.trim()).filter(Boolean);
    hiddenInput.value = selectedVals.join(', ');
    updateMultiSelectLabel(prefix, selectedVals);
}

async function loadDepartments() {
    try {
        const res = await fetch('/api/departments');
        const data = await res.json();
        if (res.ok && data.departments) {
            currentDepartments = data.departments;
            populateDepartmentDropdowns();
        }
    } catch (e) {
        console.error("Failed to load departments:", e);
    }
}

function populateDepartmentDropdowns() {
    // 1. Regular single-selects
    const singleSelects = ['reg-dept', 'modal-user-dept', 'edit-user-dept', 'manage-dept-filter', 'delete-dept-reassign-select'];
    singleSelects.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        const currentVal = el.value;
        const isFilter = id.includes('filter');
        el.innerHTML = isFilter ? '<option value="ALL">All Departments</option>' : '<option value="">Select Department...</option>';

        currentDepartments.forEach(d => {
            const opt = document.createElement('option');
            opt.value = d.Name;
            opt.textContent = d.Name;
            el.appendChild(opt);
        });

        if (currentVal) el.value = currentVal;
    });

    // 2. Multi-select options for lodge, edit, create-user, edit-user
    ['lodge', 'edit', 'create-user', 'edit-user'].forEach(prefix => {
        const container = document.getElementById(`${prefix}-dept-options`);
        if (!container) return;

        // Remember current selections if any
        let hiddenInput = null;
        if (prefix === 'lodge') hiddenInput = document.getElementById('comp-dept');
        else if (prefix === 'edit') hiddenInput = document.getElementById('edit-dept');
        else if (prefix === 'create-user') hiddenInput = document.getElementById('modal-user-dept');
        else if (prefix === 'edit-user') hiddenInput = document.getElementById('edit-user-dept');
        const currentSelections = (hiddenInput?.value || '').split(',').map(s => s.trim()).filter(Boolean);

        if (!currentDepartments.length) {
            container.innerHTML = '<div class="text-muted p-2" style="font-size: 0.85rem;">No active departments available.</div>';
            return;
        }

        container.innerHTML = currentDepartments.map(d => `
            <label class="multi-select-option" data-name="${escapeHtml(d.Name).toLowerCase()}">
                <input type="checkbox" value="${escapeHtml(d.Name)}" onchange="onDeptCheckboxChange('${prefix}')">
                <span class="option-label">${escapeHtml(d.Name)}</span>
            </label>
        `).join('');

        if (currentSelections.length) {
            setMultiSelectValues(prefix, currentSelections);
        } else {
            updateMultiSelectLabel(prefix, []);
        }
    });
}

// =============================================================================
// LODGE COMPLAINT (ALL 21 FIELDS)
// =============================================================================

async function handleLodgeComplaint(e) {
    e.preventDefault();
    const btn = document.getElementById('lodge-submit-btn');
    const form = document.getElementById('lodge-complaint-form');

    const customer_name = document.getElementById('comp-customer-name').value.trim();
    const customer_mobile_no = document.getElementById('comp-customer-mobile').value.trim();
    const invoice_no = document.getElementById('comp-invoice-no').value.trim();
    const complain_date = document.getElementById('comp-date').value.trim();
    const employee_name = document.getElementById('comp-employee-name').value.trim();
    const salesperson_name = document.getElementById('comp-salesperson-name').value.trim();
    const department_name = document.getElementById('comp-dept').value;
    const from_department = (document.getElementById('comp-from-dept')?.value || '').trim();
    const complain_types = document.getElementById('comp-type').value.trim();
    const priority = document.getElementById('comp-priority').value;
    const category = document.getElementById('comp-category').value.trim();
    const vendor_name = document.getElementById('comp-vendor-name').value.trim();
    const complain_details = document.getElementById('comp-description').value.trim();
    const remarks = document.getElementById('comp-remarks').value.trim();
    const status = document.getElementById('comp-status').value;
    const handled_by = document.getElementById('comp-handled-by').value.trim();
    const closing_date = document.getElementById('comp-closing-date').value.trim();
    const solution_given = document.getElementById('comp-solution-given').value.trim();
    const comments = document.getElementById('comp-comments').value.trim();
    const total_time_taken = document.getElementById('comp-time-taken').value.trim();
    const gr_amount = document.getElementById('comp-gr-amount').value.trim();
    const gr_expected_date = document.getElementById('comp-gr-date').value.trim();
    const linkInput = document.getElementById('comp-attachment-link');
    const fileInput = document.getElementById('comp-attachment');

    if (!department_name) {
        const toggle = document.getElementById('lodge-dept-toggle');
        if (toggle) {
            toggle.classList.add('is-invalid');
            toggle.focus();
        }
        showToast('Please select at least one department for the complaint.', 'error');
        return;
    }

    if (!from_department) {
        showToast('Please select From Department (Online, Offline, Packing, or None).', 'error');
        document.getElementById('comp-from-dept')?.focus();
        return;
    }

    if (!customer_name || !complain_types || !complain_details) {
        showToast('Please fill out required fields: Customer Name, Department, Complain Type, and Details.', 'error');
        return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span>Saving to Google Sheets...</span>';

    const formData = new FormData();
    formData.append('customer_name', customer_name);
    formData.append('customer_mobile_no', customer_mobile_no);
    formData.append('invoice_no', invoice_no);
    formData.append('complain_date', complain_date);
    formData.append('employee_name', employee_name);
    formData.append('salesperson_name', salesperson_name);
    formData.append('department_name', department_name);
    formData.append('from_department', from_department);
    formData.append('complain_types', complain_types);
    formData.append('priority', priority);
    formData.append('category', category);
    formData.append('vendor_name', vendor_name);
    formData.append('complain_details', complain_details);
    formData.append('remarks', remarks);
    formData.append('status', status);
    formData.append('handled_by', handled_by);
    formData.append('closing_date', closing_date);
    formData.append('solution_given', solution_given);
    formData.append('comments', comments);
    formData.append('total_time_taken', total_time_taken);
    formData.append('gr_amount', gr_amount);
    formData.append('gr_expected_date', gr_expected_date);

    if (fileInput && fileInput.files.length > 0) {
        formData.append('attachment', fileInput.files[0]);
    }
    if (linkInput && linkInput.value.trim()) {
        formData.append('attachment_link', linkInput.value.trim());
    }

    try {
        const res = await fetch('/api/complaints', {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
            form.reset();
            clearAllDepartments('lodge');
            const fromDeptEl = document.getElementById('comp-from-dept');
            if (fromDeptEl) fromDeptEl.value = '';
            const dateEl = document.getElementById('comp-date');
            if (dateEl) dateEl.value = new Date().toISOString().split('T')[0];
            switchView('my-complaints');
        } else {
            showToast(data.message || 'Failed to lodge complaint.', 'error');
        }
    } catch (err) {
        showToast('Network error while lodging complaint.', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<span>Submit Complaint Record</span>';
    }
}

function recalculateLodgeTimeTaken() {
    const d1 = document.getElementById('comp-date')?.value;
    const d2 = document.getElementById('comp-closing-date')?.value;
    if (d1 && d2) {
        const diff = calcDaysDiff(d1, d2);
        if (diff !== null) {
            document.getElementById('comp-time-taken').value = `${diff} day${diff !== 1 ? 's' : ''}`;
        }
    }
}

function calcDaysDiff(date1Str, date2Str) {
    try {
        const t1 = new Date(date1Str).getTime();
        const t2 = new Date(date2Str).getTime();
        if (isNaN(t1) || isNaN(t2)) return null;
        const diffTime = t2 - t1;
        const diffDays = Math.round(diffTime / (1000 * 60 * 60 * 24));
        return Math.max(0, diffDays);
    } catch (e) {
        return null;
    }
}

// =============================================================================
// DASHBOARD & COMPLAINTS LISTING
// =============================================================================

async function loadDashboardData() {
    try {
        const res = await fetch('/api/complaints');
        const data = await res.json();
        if (res.ok && data.complaints) {
            allComplaintsCache = data.complaints;
            updateStatsCounters(data.complaints);
            renderRecentTable(data.complaints.slice(0, 5));
        }
    } catch (e) {
        console.error("Dashboard load failed:", e);
    }
}

function updateStatsCounters(complaints) {
    const total = complaints.length;
    const pending = complaints.filter(c => ['Submitted', 'Under Review'].includes(c.Status)).length;
    const progress = complaints.filter(c => c.Status === 'In Progress').length;
    const resolved = complaints.filter(c => ['Resolved', 'Closed'].includes(c.Status)).length;

    document.getElementById('stat-total').textContent = total;
    document.getElementById('stat-pending').textContent = pending;
    document.getElementById('stat-progress').textContent = progress;
    document.getElementById('stat-resolved').textContent = resolved;
}

function formatDepartmentBadges(deptStr) {
    if (!deptStr || deptStr.trim() === '-' || deptStr.trim() === '') {
        return '<span class="text-muted">-</span>';
    }
    const depts = deptStr.split(',').map(s => s.trim()).filter(Boolean);
    if (!depts.length) return '<span class="text-muted">-</span>';
    return `<div class="dept-badge-list">${depts.map(d => `<span class="badge badge-dept">${escapeHtml(d)}</span>`).join('')}</div>`;
}

function renderRecentTable(complaints) {
    const tbody = document.getElementById('dashboard-recent-table');
    if (!complaints.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center py-4 text-muted">No complaints recorded yet. Click "+ Lodge Complaint" to submit one.</td></tr>';
        return;
    }

    tbody.innerHTML = complaints.map(c => `
        <tr>
            <td><strong><code>${c['Complaint ID']}</code></strong></td>
            <td>${escapeHtml(c['Complain Types'] || c.Subject || '-')}</td>
            <td>${formatDepartmentBadges(c['Department Name'] || c.Department)}</td>
            <td><span class="badge badge-prio-${(c.Priority || 'medium').toLowerCase()}">${c.Priority || 'Medium'}</span></td>
            <td><span class="badge badge-status-${(c.Status || 'submitted').toLowerCase().replace(/\s+/g, '-')}">${c.Status || 'Submitted'}</span></td>
            <td>${c['Complain Date'] || (c.Timestamp ? c.Timestamp.split(' ')[0] : '-')}</td>
            <td>
                <div class="table-row-actions">
                    <button class="btn btn-secondary btn-sm" onclick="openComplaintModal('${c['Complaint ID']}')">View Details</button>
                    <button class="btn btn-warning btn-sm" onclick="openEditComplaintModal('${c['Complaint ID']}')">Edit</button>
                </div>
            </td>
        </tr>
    `).join('');
}

async function loadMyComplaints() {
    try {
        const res = await fetch('/api/complaints');
        const data = await res.json();
        if (res.ok && data.complaints) {
            allComplaintsCache = data.complaints;
            filterMyComplaints();
        }
    } catch (e) {
        console.error("My complaints load failed:", e);
    }
}

function filterMyComplaints() {
    const search = (document.getElementById('my-search-input')?.value || '').toLowerCase().trim();
    const status = document.getElementById('my-status-filter')?.value || 'ALL';
    const priority = document.getElementById('my-priority-filter')?.value || 'ALL';

    const filtered = allComplaintsCache.filter(c => {
        const matchSearch = !search ||
            (c['Complaint ID'] || '').toLowerCase().includes(search) ||
            (c['Customer Name'] || '').toLowerCase().includes(search) ||
            (c['Customer Mobile No.'] || '').toLowerCase().includes(search) ||
            (c['Invoice No.'] || '').toLowerCase().includes(search) ||
            (c['Complain Types'] || '').toLowerCase().includes(search) ||
            (c.Subject || '').toLowerCase().includes(search) ||
            (c['Department Name'] || '').toLowerCase().includes(search) ||
            (c.Department || '').toLowerCase().includes(search) ||
            (c['Vendor Name'] || '').toLowerCase().includes(search) ||
            (c['Employee Name'] || '').toLowerCase().includes(search);

        const matchStatus = status === 'ALL' || c.Status === status;
        const matchPriority = priority === 'ALL' || c.Priority === priority;
        return matchSearch && matchStatus && matchPriority;
    });

    renderMyComplaintsTable(filtered);
}

function renderMyComplaintsTable(complaints) {
    const tbody = document.getElementById('my-complaints-table');
    if (!complaints.length) {
        tbody.innerHTML = '<tr><td colspan="10" class="text-center py-4 text-muted">No matching complaints found.</td></tr>';
        return;
    }

    const canDelete = hasPermission('complaints:delete');
    const canEditAny = hasPermission('complaints:edit');
    const isStaffOrAdmin = ['Super Admin', 'Admin', 'Department Head', 'Department Staff'].includes(currentUser?.role);

    tbody.innerHTML = complaints.map(c => {
        const isOwner = currentUser && (c['Complainant Email'] || '').toLowerCase() === (currentUser.email || '').toLowerCase();
        const canEdit = canEditAny && (isStaffOrAdmin || (isOwner && (c.Status || '').toLowerCase() === 'submitted'));
        return `
            <tr>
                <td><strong><code>${c['Complaint ID']}</code></strong></td>
                <td><strong>${escapeHtml(c['Customer Name'] || c['Complainant Name'] || '-')}</strong>${c['Customer Mobile No.'] ? `<br><small class="text-muted">${escapeHtml(c['Customer Mobile No.'])}</small>` : ''}</td>
                <td>${escapeHtml(c['Invoice No.'] || '-')}</td>
                <td>${escapeHtml(c['Complain Types'] || c.Subject || '-')}</td>
                <td>${formatDepartmentBadges(c['Department Name'] || c.Department)}</td>
                <td><span class="badge badge-prio-${(c.Priority || 'medium').toLowerCase()}">${c.Priority || 'Medium'}</span></td>
                <td><span class="badge badge-status-${(c.Status || 'submitted').toLowerCase().replace(/\s+/g, '-')}">${c.Status || 'Submitted'}</span></td>
                <td>${escapeHtml(c['Handled By'] || c['Assigned To Name'] || 'Unassigned')}</td>
                <td>${c['Complain Date'] || (c.Timestamp ? c.Timestamp.split(' ')[0] : '-')}</td>
                <td>
                    <div class="table-row-actions">
                        <button class="btn btn-secondary btn-sm" onclick="openComplaintModal('${c['Complaint ID']}')">View</button>
                        ${canEdit ? `<button class="btn btn-warning btn-sm" onclick="openEditComplaintModal('${c['Complaint ID']}')">Edit</button>` : ''}
                        ${canDelete ? `<button class="btn btn-outline-danger btn-sm" onclick="openDeleteModal('${c['Complaint ID']}')">Delete</button>` : ''}
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

async function loadManageComplaints() {
    try {
        const res = await fetch('/api/complaints');
        const data = await res.json();
        if (res.ok && data.complaints) {
            allComplaintsCache = data.complaints;
            await loadStaffUsersList();
            filterManageComplaints();
        }
    } catch (e) {
        console.error("Manage complaints load failed:", e);
    }
}

function filterManageComplaints() {
    const search = (document.getElementById('manage-search-input')?.value || '').toLowerCase().trim();
    const dept = document.getElementById('manage-dept-filter')?.value || 'ALL';
    const status = document.getElementById('manage-status-filter')?.value || 'ALL';
    const priority = document.getElementById('manage-priority-filter')?.value || 'ALL';

    const filtered = allComplaintsCache.filter(c => {
        const matchSearch = !search ||
            (c['Complaint ID'] || '').toLowerCase().includes(search) ||
            (c['Customer Name'] || '').toLowerCase().includes(search) ||
            (c['Complainant Name'] || '').toLowerCase().includes(search) ||
            (c['Customer Mobile No.'] || '').toLowerCase().includes(search) ||
            (c['Invoice No.'] || '').toLowerCase().includes(search) ||
            (c['Complain Types'] || '').toLowerCase().includes(search) ||
            (c.Subject || '').toLowerCase().includes(search) ||
            (c['Department Name'] || '').toLowerCase().includes(search) ||
            (c.Department || '').toLowerCase().includes(search) ||
            (c['Vendor Name'] || '').toLowerCase().includes(search) ||
            (c['Employee Name'] || '').toLowerCase().includes(search);

        const depts = (c['Department Name'] || c.Department || '').split(',').map(s => s.trim().toLowerCase());
        const matchDept = dept === 'ALL' || depts.includes(dept.toLowerCase());
        const matchStatus = status === 'ALL' || c.Status === status;
        const matchPriority = priority === 'ALL' || c.Priority === priority;
        return matchSearch && matchDept && matchStatus && matchPriority;
    });

    renderManageTable(filtered);
}

function renderManageTable(complaints) {
    const tbody = document.getElementById('manage-complaints-table');
    if (!complaints.length) {
        tbody.innerHTML = '<tr><td colspan="10" class="text-center py-4 text-muted">No complaints found.</td></tr>';
        return;
    }

    const canDelete = hasPermission('complaints:delete');
    const canEdit = hasPermission('complaints:edit');

    tbody.innerHTML = complaints.map(c => `
        <tr>
            <td><strong><code>${c['Complaint ID']}</code></strong></td>
            <td><strong>${escapeHtml(c['Customer Name'] || c['Complainant Name'] || '-')}</strong>${c['Customer Mobile No.'] ? `<br><small class="text-muted">${escapeHtml(c['Customer Mobile No.'])}</small>` : ''}</td>
            <td>${escapeHtml(c['Invoice No.'] || '-')}</td>
            <td>${escapeHtml(c['Complain Types'] || c.Subject || '-')}</td>
            <td>${formatDepartmentBadges(c['Department Name'] || c.Department)}</td>
            <td><span class="badge badge-prio-${(c.Priority || 'medium').toLowerCase()}">${c.Priority || 'Medium'}</span></td>
            <td><span class="badge badge-status-${(c.Status || 'submitted').toLowerCase().replace(/\s+/g, '-')}">${c.Status || 'Submitted'}</span></td>
            <td>${escapeHtml(c['Handled By'] || c['Assigned To Name'] || 'Unassigned')}</td>
            <td>${c['Complain Date'] || (c.Timestamp ? c.Timestamp.split(' ')[0] : '-')}</td>
            <td>
                <div class="table-row-actions">
                    <button class="btn btn-secondary btn-sm" onclick="openComplaintModal('${c['Complaint ID']}')">View</button>
                    ${canEdit ? `<button class="btn btn-warning btn-sm" onclick="openEditComplaintModal('${c['Complaint ID']}')">Edit</button>` : ''}
                    ${canDelete ? `<button class="btn btn-outline-danger btn-sm" onclick="openDeleteModal('${c['Complaint ID']}')">Delete</button>` : ''}
                </div>
            </td>
        </tr>
    `).join('');
}

// =============================================================================
// COMPLAINT DETAILS & TIMELINE MODAL (VIEW)
// =============================================================================

async function openComplaintModal(complaintId) {
    activeComplaintId = complaintId;
    const modal = document.getElementById('complaint-detail-modal');
    modal.style.display = 'flex';

    document.getElementById('modal-comp-id').textContent = `Loading ${complaintId}...`;
    document.getElementById('modal-comp-badges').innerHTML = '';
    document.getElementById('modal-timeline').innerHTML = '<div class="text-muted">Loading timeline...</div>';
    document.getElementById('modal-comments-list').innerHTML = '<div class="text-muted">Loading comments...</div>';

    try {
        const res = await fetch(`/api/complaints/${complaintId}`);
        const data = await res.json();
        if (res.ok) {
            renderComplaintModalData(data.complaint, data.history, data.comments);
        } else {
            showToast(data.message || 'Error loading details', 'error');
            closeComplaintModal();
        }
    } catch (e) {
        showToast('Network error loading complaint details.', 'error');
        closeComplaintModal();
    }
}

function closeComplaintModal() {
    document.getElementById('complaint-detail-modal').style.display = 'none';
    activeComplaintId = null;
    syncBodyModalOpen();
}

function openEditModalFromDetail() {
    if (activeComplaintId) {
        openEditComplaintModal(activeComplaintId);
    }
}

function openDeleteModalFromDetail() {
    if (activeComplaintId) {
        openDeleteModal(activeComplaintId);
    }
}

function renderComplaintModalData(complaint, history, comments) {
    document.getElementById('modal-comp-id').textContent = `${complaint['Complaint ID']}: ${complaint['Complain Types'] || complaint.Subject || 'Complaint'}`;
    document.getElementById('modal-comp-badges').innerHTML = `
        <span class="badge badge-status-${(complaint.Status || 'submitted').toLowerCase().replace(/\s+/g, '-')}">${complaint.Status || 'Submitted'}</span>
        <span class="badge badge-prio-${(complaint.Priority || 'medium').toLowerCase()}">${complaint.Priority || 'Medium'} Priority</span>
    `;

    // 1. Customer & Sales Details
    document.getElementById('modal-customer-name').textContent = complaint['Customer Name'] || complaint['Complainant Name'] || '-';
    document.getElementById('modal-customer-mobile').textContent = complaint['Customer Mobile No.'] || '-';
    document.getElementById('modal-invoice-no').textContent = complaint['Invoice No.'] || '-';
    document.getElementById('modal-complain-date').textContent = complaint['Complain Date'] || '-';
    document.getElementById('modal-employee-name').textContent = complaint['Employee Name'] || '-';
    document.getElementById('modal-salesperson-name').textContent = complaint['Salesperson Name'] || '-';
    document.getElementById('modal-email').textContent = complaint['Complainant Email'] || '-';

    // 2. Department & Categorization
    const deptVal = complaint['Department Name'] || complaint.Department || '';
    const deptList = deptVal.split(',').map(s => s.trim()).filter(Boolean);
    if (deptList.length) {
        document.getElementById('modal-dept').innerHTML = deptList.map(d => `<span class="badge badge-dept">${escapeHtml(d)}</span>`).join(' ');
    } else {
        document.getElementById('modal-dept').textContent = '-';
    }
    const fromDeptEl = document.getElementById('modal-from-dept');
    if (fromDeptEl) {
        fromDeptEl.textContent = complaint['From Department'] || complaint.from_department || '-';
    }
    document.getElementById('modal-complain-type').textContent = complaint['Complain Types'] || complaint.Subject || '-';
    document.getElementById('modal-category').textContent = complaint.Category || '-';
    document.getElementById('modal-vendor-name').textContent = complaint['Vendor Name'] || '-';

    // 3. Issue Description & Remarks
    document.getElementById('modal-description').textContent = complaint['Complain Details'] || complaint.Description || '-';
    const remarksBox = document.getElementById('modal-remarks-box');
    const remarksEl = document.getElementById('modal-remarks');
    if (complaint.Remarks && complaint.Remarks.trim()) {
        remarksBox.style.display = 'block';
        remarksEl.textContent = complaint.Remarks;
    } else {
        remarksBox.style.display = 'none';
    }

    // 4. Resolution & Goods Return (GR)
    document.getElementById('modal-status-text').textContent = complaint.Status || 'Submitted';
    document.getElementById('modal-assigned').textContent = complaint['Handled By'] || complaint['Assigned To Name'] || 'Unassigned';
    document.getElementById('modal-closing-date').textContent = complaint['Closing Date'] || '-';
    document.getElementById('modal-time-taken').textContent = complaint['Total Time Taken'] || '-';
    document.getElementById('modal-gr-amount').textContent = complaint['GR Amount'] ? `₹ ${Number(complaint['GR Amount']).toLocaleString('en-IN', {minimumFractionDigits: 2})}` : '-';
    document.getElementById('modal-gr-date').textContent = complaint['GR Expected Date'] || '-';
    document.getElementById('modal-solution-given').textContent = complaint['Solution Given'] || '-';
    document.getElementById('modal-record-comments').textContent = complaint.Comments || '-';

    // 5. Timestamps & Attachment
    document.getElementById('modal-timestamp').textContent = complaint.Timestamp || '-';
    document.getElementById('modal-updated').textContent = complaint['Last Updated At'] || complaint.Timestamp || '-';

    const attachLink = document.getElementById('modal-attachment-link');
    const attachNone = document.getElementById('modal-attachment-none');
    if (complaint['Attachment Link']) {
        attachLink.style.display = 'inline-block';
        attachLink.href = complaint['Attachment Link'];
        attachNone.style.display = 'none';
    } else {
        attachLink.style.display = 'none';
        attachNone.style.display = 'inline';
    }

    // Permissions for Edit & Delete buttons
    const canDelete = hasPermission('complaints:delete');
    const canEditAny = hasPermission('complaints:edit');
    const isOwner = currentUser && (complaint['Complainant Email'] || '').toLowerCase() === (currentUser.email || '').toLowerCase();
    const isSubmitted = (complaint.Status || '').toLowerCase() === 'submitted';
    const isStaffOrAdmin = ['Super Admin', 'Admin', 'Department Head', 'Department Staff'].includes(currentUser?.role);

    const editBtn = document.getElementById('modal-btn-edit');
    if (editBtn) {
        editBtn.style.display = (canEditAny && (isStaffOrAdmin || (isOwner && isSubmitted))) ? 'inline-flex' : 'none';
    }

    const deleteBtn = document.getElementById('modal-btn-delete');
    if (deleteBtn) {
        deleteBtn.style.display = canDelete ? 'inline-flex' : 'none';
    }

    // Staff Controls
    const canChangeStatus = hasPermission('complaints:change_status') || hasPermission('complaints:close') || hasPermission('complaints:reopen');
    const canAssign = hasPermission('complaints:assign');
    const staffBox = document.getElementById('modal-staff-controls');
    if (staffBox) {
        staffBox.style.display = (canChangeStatus || canAssign) ? 'block' : 'none';
        const statusRow = document.getElementById('modal-status-select')?.closest('.form-group');
        if (statusRow) statusRow.style.display = canChangeStatus ? 'block' : 'none';
        const assignRow = document.getElementById('modal-assign-select')?.closest('.form-group');
        if (assignRow) assignRow.style.display = canAssign ? 'block' : 'none';
        const remarksInput = document.getElementById('modal-status-remarks')?.closest('.form-group');
        if (remarksInput) remarksInput.style.display = canChangeStatus ? 'block' : 'none';

        if (canChangeStatus) {
            document.getElementById('modal-status-select').value = complaint.Status || 'Submitted';
        }
        if (canAssign) {
            populateAssignSelect();
        }
    }

    // Comments Form & Type Controls
    const commentForm = document.getElementById('modal-comment-form');
    if (commentForm) {
        const canComment = hasPermission('comments:add') || hasPermission('comments:add_internal');
        commentForm.style.display = canComment ? 'block' : 'none';
    }
    const commentTypeSelect = document.getElementById('modal-comment-type');
    if (commentTypeSelect) {
        const canInternal = hasPermission('comments:add_internal');
        commentTypeSelect.style.display = canInternal ? 'inline-block' : 'none';
        if (!canInternal) {
            commentTypeSelect.value = 'Public Update';
        }
    }

    // Timeline History
    const timelineEl = document.getElementById('modal-timeline');
    if (!history || !history.length) {
        timelineEl.innerHTML = '<div class="text-muted">No timeline history recorded.</div>';
    } else {
        timelineEl.innerHTML = history.map(h => `
            <div class="timeline-item">
                <div class="timeline-time">${h.Timestamp}</div>
                <div class="timeline-content">${escapeHtml(h['Old Status'])} &rarr; <strong>${escapeHtml(h['New Status'])}</strong> by ${escapeHtml(h['Changed By Name'])}</div>
                ${h.Remarks ? `<div class="timeline-remarks">"${escapeHtml(h.Remarks)}"</div>` : ''}
            </div>
        `).join('');
    }

    // Comments
    renderCommentsList(comments);
}

// =============================================================================
// EDIT COMPLAINT MODAL (ALL 21 FIELDS)
// =============================================================================

let activeEditComplaintId = null;

async function openEditComplaintModal(complaintId) {
    activeEditComplaintId = complaintId;
    let complaint = allComplaintsCache.find(c => c['Complaint ID'] === complaintId);

    if (!complaint) {
        try {
            const res = await fetch(`/api/complaints/${complaintId}`);
            if (res.ok) {
                const data = await res.json();
                complaint = data.complaint;
            }
        } catch (e) {
            console.error(e);
        }
    }

    if (!complaint) {
        showToast('Complaint not found.', 'error');
        return;
    }

    document.getElementById('edit-modal-title').textContent = `Edit Complaint: ${complaint['Complaint ID']}`;

    // Fill all 21 fields
    document.getElementById('edit-customer-name').value = complaint['Customer Name'] || complaint['Complainant Name'] || '';
    document.getElementById('edit-customer-mobile').value = complaint['Customer Mobile No.'] || '';
    document.getElementById('edit-invoice-no').value = complaint['Invoice No.'] || '';
    document.getElementById('edit-complain-date').value = (complaint['Complain Date'] || '').split(' ')[0] || '';
    document.getElementById('edit-employee-name').value = complaint['Employee Name'] || '';
    document.getElementById('edit-salesperson-name').value = complaint['Salesperson Name'] || '';

    // Department & classification
    populateDepartmentDropdowns();
    setMultiSelectValues('edit', complaint['Department Name'] || complaint.Department || '');
    const fromDeptVal = complaint['From Department'] || complaint.from_department || 'None';
    const editFromDept = document.getElementById('edit-from-dept');
    if (editFromDept) {
        editFromDept.value = ['Online', 'Offline', 'Packing', 'None'].includes(fromDeptVal) ? fromDeptVal : 'None';
    }
    document.getElementById('edit-complain-type').value = complaint['Complain Types'] || complaint.Subject || '';
    document.getElementById('edit-priority').value = complaint.Priority || 'Medium';
    document.getElementById('edit-category').value = complaint.Category || '';
    document.getElementById('edit-vendor-name').value = complaint['Vendor Name'] || '';

    // Initialize searchable options with historical fallback
    renderSearchableDropdownOptions('edit-complain-type', masterOptionsCache.complaint_types, document.getElementById('edit-complain-type').value);
    renderSearchableDropdownOptions('edit-salesperson-name', masterOptionsCache.salespersons, document.getElementById('edit-salesperson-name').value);

    // Description & remarks
    document.getElementById('edit-description').value = complaint['Complain Details'] || complaint.Description || '';
    document.getElementById('edit-remarks').value = complaint.Remarks || '';

    // Resolution & GR tracking
    document.getElementById('edit-status').value = complaint.Status || 'Submitted';
    document.getElementById('edit-handled-by').value = complaint['Handled By'] || complaint['Assigned To Name'] || '';
    document.getElementById('edit-closing-date').value = (complaint['Closing Date'] || '').split(' ')[0] || '';
    document.getElementById('edit-solution-given').value = complaint['Solution Given'] || '';
    document.getElementById('edit-comments').value = complaint.Comments || '';
    document.getElementById('edit-time-taken').value = complaint['Total Time Taken'] || '';
    document.getElementById('edit-gr-amount').value = complaint['GR Amount'] || '';
    document.getElementById('edit-gr-date').value = (complaint['GR Expected Date'] || '').split(' ')[0] || '';

    // Attachment
    document.getElementById('edit-attachment-link').value = complaint['Attachment Link'] || '';

    document.getElementById('edit-complaint-modal').style.display = 'flex';
}

function closeEditComplaintModal() {
    document.getElementById('edit-complaint-modal').style.display = 'none';
    activeEditComplaintId = null;
    syncBodyModalOpen();
}

function recalculateEditTimeTaken() {
    const d1 = document.getElementById('edit-complain-date')?.value;
    const d2 = document.getElementById('edit-closing-date')?.value;
    if (d1 && d2) {
        const diff = calcDaysDiff(d1, d2);
        if (diff !== null) {
            document.getElementById('edit-time-taken').value = `${diff} day${diff !== 1 ? 's' : ''}`;
        }
    }
}

async function submitEditComplaint(e) {
    e.preventDefault();
    if (!activeEditComplaintId) return;

    const department_name = document.getElementById('edit-dept').value.trim();
    if (!department_name) {
        const toggle = document.getElementById('edit-dept-toggle');
        if (toggle) {
            toggle.classList.add('is-invalid');
            toggle.focus();
        }
        showToast('Please select at least one department for the complaint.', 'error');
        return;
    }

    const btn = document.getElementById('edit-submit-btn');
    btn.disabled = true;
    btn.textContent = 'Saving changes...';

    const payload = {
        customer_name: document.getElementById('edit-customer-name').value.trim(),
        customer_mobile_no: document.getElementById('edit-customer-mobile').value.trim(),
        invoice_no: document.getElementById('edit-invoice-no').value.trim(),
        complain_date: document.getElementById('edit-complain-date').value.trim(),
        employee_name: document.getElementById('edit-employee-name').value.trim(),
        salesperson_name: document.getElementById('edit-salesperson-name').value.trim(),
        department_name: document.getElementById('edit-dept').value,
        from_department: (document.getElementById('edit-from-dept')?.value || 'None').trim(),
        complain_types: document.getElementById('edit-complain-type').value.trim(),
        priority: document.getElementById('edit-priority').value,
        category: document.getElementById('edit-category').value.trim(),
        vendor_name: document.getElementById('edit-vendor-name').value.trim(),
        complain_details: document.getElementById('edit-description').value.trim(),
        remarks: document.getElementById('edit-remarks').value.trim(),
        status: document.getElementById('edit-status').value,
        handled_by: document.getElementById('edit-handled-by').value.trim(),
        closing_date: document.getElementById('edit-closing-date').value.trim(),
        solution_given: document.getElementById('edit-solution-given').value.trim(),
        comments: document.getElementById('edit-comments').value.trim(),
        total_time_taken: document.getElementById('edit-time-taken').value.trim(),
        gr_amount: document.getElementById('edit-gr-amount').value.trim(),
        gr_expected_date: document.getElementById('edit-gr-date').value.trim(),
        attachment_link: document.getElementById('edit-attachment-link').value.trim()
    };

    try {
        const res = await fetch(`/api/complaints/${activeEditComplaintId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
            closeEditComplaintModal();
            // Refresh detail modal if open
            if (activeComplaintId === activeEditComplaintId) {
                await openComplaintModal(activeComplaintId);
            }
            // Refresh tables
            loadDashboardData();
            loadMyComplaints();
            loadManageComplaints();
        } else {
            showToast(data.message || 'Failed to update complaint.', 'error');
        }
    } catch (err) {
        showToast('Network error while updating complaint.', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Save Changes to Google Sheets';
    }
}

// =============================================================================
// DELETE CONFIRMATION MODAL
// =============================================================================

let complaintToDeleteId = null;

function openDeleteModal(complaintId) {
    complaintToDeleteId = complaintId;
    const complaint = allComplaintsCache.find(c => c['Complaint ID'] === complaintId) || {};

    document.getElementById('delete-modal-comp-id').textContent = complaintId;
    document.getElementById('delete-modal-customer').textContent = complaint['Customer Name'] || complaint['Complainant Name'] || '-';
    document.getElementById('delete-modal-subject').textContent = complaint['Complain Types'] || complaint.Subject || '-';

    document.getElementById('delete-confirm-modal').style.display = 'flex';
}

function closeDeleteConfirmModal() {
    document.getElementById('delete-confirm-modal').style.display = 'none';
    complaintToDeleteId = null;
    syncBodyModalOpen();
}

async function executeDeleteComplaint() {
    if (!complaintToDeleteId) return;
    const btn = document.getElementById('delete-confirm-btn');
    btn.disabled = true;
    btn.textContent = 'Deleting...';

    try {
        const res = await fetch(`/api/complaints/${complaintToDeleteId}`, {
            method: 'DELETE'
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
            const deletedId = complaintToDeleteId;
            closeDeleteConfirmModal();
            if (activeComplaintId === deletedId) {
                closeComplaintModal();
            }
            // Refresh tables
            loadDashboardData();
            loadMyComplaints();
            loadManageComplaints();
        } else {
            showToast(data.message || 'Failed to delete complaint.', 'error');
        }
    } catch (err) {
        showToast('Network error while deleting complaint.', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Confirm & Delete';
    }
}

function renderCommentsList(comments) {
    const listEl = document.getElementById('modal-comments-list');
    if (!comments.length) {
        listEl.innerHTML = '<div class="text-muted">No comments or activity notes yet.</div>';
        return;
    }

    listEl.innerHTML = comments.map(c => `
        <div class="comment-card ${c['Comment Type'] === 'Internal Note' ? 'internal-note' : ''}">
            <div class="comment-header">
                <span class="comment-author">${escapeHtml(c['Author Name'])} (${escapeHtml(c['Author Role'])}) ${c['Comment Type'] === 'Internal Note' ? '🔒 [Internal Note]' : ''}</span>
                <span>${c.Timestamp}</span>
            </div>
            <div class="comment-text">${escapeHtml(c.Message)}</div>
        </div>
    `).join('');
}

async function submitStatusUpdate() {
    if (!activeComplaintId) return;
    const newStatus = document.getElementById('modal-status-select').value;
    const remarks = document.getElementById('modal-status-remarks').value.trim();

    try {
        const res = await fetch(`/api/complaints/${activeComplaintId}/status`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: newStatus, remarks })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
            document.getElementById('modal-status-remarks').value = '';
            await openComplaintModal(activeComplaintId);
            // Refresh underlying tables
            loadManageComplaints();
        } else {
            showToast(data.message || 'Status update failed', 'error');
        }
    } catch (e) {
        showToast('Network error updating status.', 'error');
    }
}

async function submitAssignment() {
    if (!activeComplaintId) return;
    const assignSelect = document.getElementById('modal-assign-select');
    const assignedEmail = assignSelect.value;
    const assignedName = assignSelect.options[assignSelect.selectedIndex]?.text || '';

    if (!assignedEmail) {
        showToast('Please select a staff member.', 'error');
        return;
    }

    try {
        const res = await fetch(`/api/complaints/${activeComplaintId}/assign`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ assigned_to_email: assignedEmail, assigned_to_name: assignedName })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
            await openComplaintModal(activeComplaintId);
            loadManageComplaints();
        } else {
            showToast(data.message || 'Assignment failed', 'error');
        }
    } catch (e) {
        showToast('Network error during assignment.', 'error');
    }
}

async function submitComment(e) {
    e.preventDefault();
    if (!activeComplaintId) return;
    const msgInput = document.getElementById('modal-comment-msg');
    const typeSelect = document.getElementById('modal-comment-type');
    const message = msgInput.value.trim();
    const comment_type = typeSelect?.value || 'Public Update';

    if (!message) return;

    try {
        const res = await fetch(`/api/complaints/${activeComplaintId}/comments`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message, comment_type })
        });
        const data = await res.json();
        if (res.ok) {
            showToast('Comment posted.', 'success');
            msgInput.value = '';
            await openComplaintModal(activeComplaintId);
        } else {
            showToast(data.message || 'Failed to post comment', 'error');
        }
    } catch (err) {
        showToast('Network error posting comment.', 'error');
    }
}

async function loadStaffUsersList() {
    try {
        const res = await fetch('/api/users');
        if (res.ok) {
            const data = await res.json();
            allStaffUsers = (data.users || []).filter(u => ['Super Admin', 'Admin', 'Department Head', 'Department Staff', 'Support Staff'].includes(u.Role));
        }
    } catch (e) {
        allStaffUsers = [];
    }
}

function populateAssignSelect() {
    const sel = document.getElementById('modal-assign-select');
    if (!sel) return;
    sel.innerHTML = '<option value="">Select Assignee...</option>';
    allStaffUsers.forEach(u => {
        const opt = document.createElement('option');
        opt.value = u.Email;
        opt.textContent = `${u['Full Name']} (${u.Role})`;
        sel.appendChild(opt);
    });
}

// =============================================================================
// ADMIN SETTINGS & MANAGEMENT
// =============================================================================

async function loadSettingsData() {
    await checkSystemStatus();
    await loadDriveStatus();
    loadAdminUsers();
    loadAdminDepts();
}

let currentUsersList = [];

async function loadAdminUsers() {
    try {
        const res = await fetch('/api/users');
        const data = await res.json();
        if (res.ok && data.users) {
            currentUsersList = data.users;
            renderUsersTable();
        } else {
            const tbody = document.getElementById('settings-users-table');
            if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="text-center py-4 text-danger">Failed to load users.</td></tr>';
        }
    } catch (e) {
        console.error("Failed to load users:", e);
        const tbody = document.getElementById('settings-users-table');
        if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="text-center py-4 text-danger">Error loading users.</td></tr>';
    }
}

function renderUsersTable() {
    const tbody = document.getElementById('settings-users-table');
    if (!tbody) return;
    if (!currentUsersList || currentUsersList.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center py-4 text-muted">No users found.</td></tr>';
        return;
    }

    const filterVal = document.getElementById('users-status-filter')?.value || 'ALL';
    const isSuperAdmin = currentUser && currentUser.role === 'Super Admin';
    const isAdmin = currentUser && (currentUser.role === 'Admin' || isSuperAdmin);

    const filteredUsers = currentUsersList.filter(u => {
        const rawStatus = String(u['Is Active'] || 'TRUE').toUpperCase();
        if (filterVal === 'ACTIVE') return rawStatus === 'TRUE';
        if (filterVal === 'INACTIVE') return rawStatus === 'FALSE';
        if (filterVal === 'ARCHIVED') return rawStatus === 'ARCHIVED';
        return true;
    });

    if (filteredUsers.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7" class="text-center py-4 text-muted">No ${escapeHtml(filterVal.toLowerCase())} users found.</td></tr>`;
        return;
    }

    tbody.innerHTML = filteredUsers.map(u => {
        const rawStatus = String(u['Is Active'] || 'TRUE').toUpperCase();
        const isTargetSuper = u.Role === 'Super Admin';
        const isSelf = currentUser && (currentUser.user_id === u['User ID'] || currentUser.email === u.Email);
        const canEdit = isSuperAdmin || (isAdmin && !isTargetSuper);
        const canDelete = isSuperAdmin || (isAdmin && !isTargetSuper);

        let statusBadge = '';
        if (rawStatus === 'TRUE') {
            statusBadge = '<span class="badge badge-active">Active</span>';
        } else if (rawStatus === 'ARCHIVED') {
            statusBadge = '<span class="badge" style="background: #e2e8f0; color: #475569; border: 1px solid #cbd5e1;">Archived</span>';
        } else {
            statusBadge = '<span class="badge badge-inactive">Inactive</span>';
        }

        const roleBadgeClass = u.Role === 'Super Admin'
            ? 'badge-status-in-progress'
            : u.Role === 'Admin'
            ? 'badge-prio-high'
            : u.Role === 'Support Staff'
            ? 'badge-info'
            : 'badge-prio-low';

        return `
            <tr>
                <td><code>${escapeHtml(u['User ID'])}</code></td>
                <td><strong>${escapeHtml(u['Full Name'])}</strong></td>
                <td>${escapeHtml(u.Email)}</td>
                <td><span class="badge ${roleBadgeClass}">${escapeHtml(u.Role)}</span></td>
                <td>${escapeHtml(u.Department || 'General')}</td>
                <td>${statusBadge}</td>
                <td>
                    <div class="table-row-actions" style="display: flex; gap: 6px; align-items: center;">
                        ${canEdit ? `
                            <button type="button" class="btn btn-secondary btn-sm" onclick="openEditUserModal('${escapeHtml(u['User ID'])}')">
                                Edit
                            </button>
                        ` : `
                            <span class="text-muted text-sm" title="Super Admin accounts can only be edited by Super Admins">Protected</span>
                        `}
                        ${isSelf ? `
                            <button type="button" class="btn btn-outline-secondary btn-sm" disabled title="You cannot delete your own logged-in account">
                                Delete
                            </button>
                        ` : canDelete ? `
                            <button type="button" class="btn btn-outline-danger btn-sm" onclick="openDeleteUserModal('${escapeHtml(u['User ID'])}')">
                                Delete
                            </button>
                        ` : `
                            <button type="button" class="btn btn-outline-secondary btn-sm" disabled title="Super Admin accounts can only be deleted by Super Admins">
                                Delete
                            </button>
                        `}
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function syncBodyModalOpen() {
    const activeModals = document.querySelectorAll('.modal-backdrop');
    let anyOpen = false;
    for (let i = 0; i < activeModals.length; i++) {
        const m = activeModals[i];
        if (m.style.display && m.style.display !== 'none') {
            anyOpen = true;
            break;
        }
    }
    if (anyOpen) {
        document.body.classList.add('modal-open');
    } else {
        document.body.classList.remove('modal-open');
    }
}

function openCreateUserModal() {
    document.getElementById('modal-user-name').value = '';
    document.getElementById('modal-user-email').value = '';
    document.getElementById('modal-user-password').value = '';
    populateDepartmentDropdowns();
    setMultiSelectValues('create-user', ['General']);
    loadRolesDropdownForUsers();
    const modal = document.getElementById('create-user-modal');
    modal.style.display = 'flex';
    document.body.classList.add('modal-open');
    const modalBody = modal.querySelector('.modal-body');
    if (modalBody) {
        modalBody.scrollTop = 0;
    }
}
function closeCreateUserModal() {
    document.getElementById('create-user-modal').style.display = 'none';
    syncBodyModalOpen();
}

async function submitCreateUser(e) {
    e.preventDefault();
    const full_name = document.getElementById('modal-user-name').value.trim();
    const email = document.getElementById('modal-user-email').value.trim();
    const role = document.getElementById('modal-user-role').value;
    const department = document.getElementById('modal-user-dept').value || 'General';
    const password = document.getElementById('modal-user-password').value;

    try {
        const res = await fetch('/api/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ full_name, email, role, department, password })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
            closeCreateUserModal();
            loadAdminUsers();
        } else {
            showToast(data.message || 'Failed to create user', 'error');
        }
    } catch (err) {
        showToast('Network error creating user.', 'error');
    }
}

function openEditUserModal(userId) {
    const user = currentUsersList.find(u => u['User ID'] === userId);
    if (!user) {
        showToast("User details not found. Please refresh the users list.", "error");
        return;
    }

    const isSuperAdmin = currentUser && currentUser.role === 'Super Admin';
    const isTargetSuper = user.Role === 'Super Admin';

    if (isTargetSuper && !isSuperAdmin) {
        showToast("Access forbidden: Super Admin accounts can only be edited by Super Admins.", "error");
        return;
    }

    document.getElementById('edit-user-id').value = user['User ID'];
    document.getElementById('edit-user-id-display').value = user['User ID'];
    document.getElementById('edit-user-name').value = user['Full Name'] || '';
    document.getElementById('edit-user-email').value = user.Email || '';
    document.getElementById('edit-user-password').value = '';

    // Populate and select multi-department
    populateDepartmentDropdowns();
    setMultiSelectValues('edit-user', user.Department || 'General');

    // Dynamic Role options & RBAC guard
    loadRolesDropdownForUsers();
    const roleSelect = document.getElementById('edit-user-role');
    const superOpt = document.getElementById('opt-edit-role-super-admin');
    const roleNote = document.getElementById('edit-user-role-note');

    if (!isSuperAdmin) {
        if (superOpt) superOpt.disabled = true;
        if (roleNote) roleNote.textContent = 'Only Super Admins can assign or promote to the Super Admin role.';
    } else {
        if (superOpt) superOpt.disabled = false;
        if (roleNote) roleNote.textContent = '';
    }
    roleSelect.value = user.Role || 'User';

    // Account status & self-deactivation guard
    const statusSelect = document.getElementById('edit-user-status');
    const statusNote = document.getElementById('edit-user-status-note');
    const isSelf = currentUser && (currentUser.user_id === user['User ID'] || currentUser.email === user.Email);

    if (isSelf) {
        statusSelect.value = 'TRUE';
        statusSelect.disabled = true;
        if (statusNote) statusNote.textContent = 'You cannot deactivate your own active account.';
    } else {
        statusSelect.disabled = false;
        statusSelect.value = String(user['Is Active'] || 'TRUE').toUpperCase() === 'FALSE' ? 'FALSE' : 'TRUE';
        if (statusNote) statusNote.textContent = '';
    }

    const modal = document.getElementById('edit-user-modal');
    modal.style.display = 'flex';
    document.body.classList.add('modal-open');
    const modalBody = modal.querySelector('.modal-body');
    if (modalBody) {
        modalBody.scrollTop = 0;
    }
}

function closeEditUserModal() {
    document.getElementById('edit-user-modal').style.display = 'none';
    syncBodyModalOpen();
}

async function submitEditUser(e) {
    e.preventDefault();
    const userId = document.getElementById('edit-user-id').value.trim();
    const fullName = document.getElementById('edit-user-name').value.trim();
    const email = document.getElementById('edit-user-email').value.trim().toLowerCase();
    const dept = document.getElementById('edit-user-dept').value || 'General';
    const role = document.getElementById('edit-user-role').value;
    const statusSelect = document.getElementById('edit-user-status');
    const isActive = statusSelect.disabled ? 'TRUE' : statusSelect.value;
    const newPassword = document.getElementById('edit-user-password').value.trim();

    if (!fullName || !email) {
        showToast("Full Name and Email Address are required.", "error");
        return;
    }

    if (newPassword && newPassword.length < 6) {
        showToast("Reset password must be at least 6 characters.", "error");
        return;
    }

    const submitBtn = document.getElementById('edit-user-submit-btn');
    const originalText = submitBtn.textContent;
    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving...';

    const payload = {
        full_name: fullName,
        email: email,
        department: dept,
        role: role,
        is_active: isActive
    };
    if (newPassword) {
        payload.new_password = newPassword;
    }

    try {
        const res = await fetch(`/api/users/${encodeURIComponent(userId)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (res.ok && data.status === 'success') {
            showToast(data.message || `User '${fullName}' updated successfully.`, 'success');
            closeEditUserModal();
            await loadAdminUsers();

            // If current logged-in user edited their own profile, sync display name
            if (currentUser && (currentUser.user_id === userId || currentUser.email === email)) {
                currentUser.name = fullName;
                currentUser.email = email;
                currentUser.role = role;
                currentUser.department = dept;
                renderUserInterface();
            }
        } else {
            showToast(data.message || "Failed to update user.", "error");
        }
    } catch (err) {
        console.error("Error updating user:", err);
        showToast("Network error updating user.", "error");
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = originalText;
    }
}

let activeDeleteUser = null;

async function openDeleteUserModal(userId) {
    const user = currentUsersList.find(u => u['User ID'] === userId);
    if (!user) {
        showToast("User details not found.", "error");
        return;
    }

    const isSuperAdmin = currentUser && currentUser.role === 'Super Admin';
    const isTargetSuper = user.Role === 'Super Admin';
    const isSelf = currentUser && (currentUser.user_id === user['User ID'] || currentUser.email === user.Email);

    if (isSelf) {
        showToast("You cannot delete your own logged-in account.", "error");
        return;
    }

    if (isTargetSuper && !isSuperAdmin) {
        showToast("Access forbidden: Super Admin accounts can only be deleted by Super Admins.", "error");
        return;
    }

    activeDeleteUser = user;

    document.getElementById('delete-user-id').value = user['User ID'];
    document.getElementById('delete-user-name-display').textContent = user['Full Name'] || '-';
    document.getElementById('delete-user-id-display').textContent = user['User ID'];
    document.getElementById('delete-user-email-display').textContent = user.Email || '-';
    document.getElementById('delete-user-role-display').textContent = user.Role || '-';
    document.getElementById('delete-user-dept-display').textContent = user.Department || 'General';

    const rawStatus = String(user['Is Active'] || 'TRUE').toUpperCase();
    document.getElementById('delete-user-status-display').textContent = rawStatus === 'TRUE' ? 'Active' : (rawStatus === 'ARCHIVED' ? 'Archived' : 'Inactive');

    document.getElementById('delete-user-reason').value = '';
    document.getElementById('delete-user-confirm-input').value = '';
    document.getElementById('delete-type-soft').checked = true;

    // Enable/disable Permanent Delete option based on role
    const permRadio = document.getElementById('delete-type-permanent');
    const permLabel = document.getElementById('delete-type-perm-label');
    if (!isSuperAdmin) {
        permRadio.disabled = true;
        permLabel.style.opacity = '0.5';
        permLabel.style.cursor = 'not-allowed';
        permLabel.title = "Permanent deletion is restricted to Super Admins.";
    } else {
        permRadio.disabled = false;
        permLabel.style.opacity = '1';
        permLabel.style.cursor = 'pointer';
        permLabel.title = "";
    }

    toggleDeleteTypeUI();

    const modal = document.getElementById('delete-user-modal');
    modal.style.display = 'flex';
    document.body.classList.add('modal-open');
    const modalBody = modal.querySelector('.modal-body');
    if (modalBody) {
        modalBody.scrollTop = 0;
    }

    // Fetch live usage analysis
    const usageBox = document.getElementById('delete-user-usage-box');
    const usageLoading = document.getElementById('delete-user-usage-loading');
    usageBox.style.display = 'none';
    usageLoading.style.display = 'block';

    try {
        const res = await fetch(`/api/users/${encodeURIComponent(userId)}/usage`);
        const data = await res.json();
        usageLoading.style.display = 'none';
        if (res.ok && data.usage) {
            document.getElementById('delete-user-lodged-count').textContent = data.usage.lodged_complaints_count || 0;
            document.getElementById('delete-user-handled-count').textContent = data.usage.handled_complaints_count || 0;
            document.getElementById('delete-user-comments-count').textContent = (data.usage.comments_count || 0) + (data.usage.history_count || 0);
            usageBox.style.display = 'block';
        }
    } catch (e) {
        usageLoading.style.display = 'none';
    }
}

function closeDeleteUserModal() {
    const modal = document.getElementById('delete-user-modal');
    if (modal) modal.style.display = 'none';
    activeDeleteUser = null;
    syncBodyModalOpen();
}

function toggleDeleteTypeUI() {
    const isPermanent = document.getElementById('delete-type-permanent')?.checked;
    const confirmBox = document.getElementById('delete-user-confirm-box');
    const submitBtn = document.getElementById('delete-user-submit-btn');

    if (isPermanent) {
        if (confirmBox) confirmBox.style.display = 'block';
        if (submitBtn) {
            submitBtn.textContent = 'Permanently Delete User';
            submitBtn.className = 'btn btn-danger';
        }
    } else {
        if (confirmBox) confirmBox.style.display = 'none';
        if (submitBtn) {
            submitBtn.textContent = 'Archive User';
            submitBtn.className = 'btn btn-warning';
        }
    }
}

async function submitDeleteUser(e) {
    e.preventDefault();
    if (!activeDeleteUser) return;

    const userId = document.getElementById('delete-user-id').value.trim();
    const action = document.querySelector('input[name="delete_type"]:checked')?.value || 'soft';
    const reason = document.getElementById('delete-user-reason').value.trim();
    const confirmText = document.getElementById('delete-user-confirm-input').value.trim();

    if (!reason) {
        showToast("Please provide a reason for the action.", "error");
        return;
    }

    if (action === 'permanent' && confirmText.toUpperCase() !== 'DELETE') {
        showToast("Please type DELETE in the confirmation box to permanently delete this user.", "error");
        return;
    }

    const submitBtn = document.getElementById('delete-user-submit-btn');
    const originalText = submitBtn.textContent;
    submitBtn.disabled = true;
    submitBtn.textContent = action === 'permanent' ? 'Deleting...' : 'Archiving...';

    try {
        const res = await fetch(`/api/users/${encodeURIComponent(userId)}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                action: action,
                reason: reason,
                confirm: confirmText
            })
        });
        const data = await res.json();

        if (res.ok && data.status === 'success') {
            showToast(data.message || "User operation completed successfully.", "success");
            closeDeleteUserModal();
            await loadAdminUsers();
        } else {
            showToast(data.message || "Failed to process user deletion.", "error");
        }
    } catch (err) {
        console.error("Error executing user deletion:", err);
        showToast("Network error executing user deletion.", "error");
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = originalText;
    }
}

// =============================================================================
// DEPARTMENT MANAGEMENT (ADMIN ONLY)
// =============================================================================

let activeEditDept = null;
let activeDeleteDeptId = null;
let activeDeleteDeptName = '';
let activeDeleteLinkedCount = 0;

async function loadAdminDepts() {
    const tbody = document.getElementById('settings-depts-table');
    if (!tbody) return;
    try {
        const res = await fetch('/api/departments?all=true');
        const data = await res.json();
        const depts = (res.ok && data.departments) ? data.departments : currentDepartments;

        if (!depts.length) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-center py-4 text-muted">No departments configured yet.</td></tr>';
            return;
        }

        tbody.innerHTML = depts.map(d => {
            const isActive = String(d['Is Active']).toUpperCase() !== 'FALSE';
            const deptId = d['Department ID'] || '';
            const deptName = d.Name || '';
            return `
                <tr>
                    <td><code>${escapeHtml(deptId)}</code></td>
                    <td><strong>${escapeHtml(deptName)}</strong></td>
                    <td>${escapeHtml(d.Description || '-')}</td>
                    <td>${escapeHtml(d['Lead Email'] || '-')}</td>
                    <td><span class="${isActive ? 'badge-active' : 'badge-inactive'}">${isActive ? 'Active' : 'Inactive'}</span></td>
                    <td>
                        <div class="table-row-actions">
                            <button class="btn btn-warning btn-sm" onclick="openEditDeptModal('${escapeHtml(deptId)}')">Edit</button>
                            <button class="btn btn-outline-danger btn-sm" onclick="openDeleteDeptModal('${escapeHtml(deptId)}')">Delete</button>
                        </div>
                    </td>
                </tr>
            `;
        }).join('');
    } catch (e) {
        console.error("Failed to load admin departments:", e);
        tbody.innerHTML = '<tr><td colspan="6" class="text-center py-4 text-danger">Error loading departments.</td></tr>';
    }
}

function openCreateDeptModal() {
    document.getElementById('modal-dept-name').value = '';
    document.getElementById('modal-dept-desc').value = '';
    document.getElementById('modal-dept-lead').value = '';
    document.getElementById('create-dept-modal').style.display = 'flex';
}

function closeCreateDeptModal() {
    document.getElementById('create-dept-modal').style.display = 'none';
    syncBodyModalOpen();
}

async function submitCreateDept(e) {
    e.preventDefault();
    const name = document.getElementById('modal-dept-name').value.trim();
    const description = document.getElementById('modal-dept-desc').value.trim();
    const lead_email = document.getElementById('modal-dept-lead').value.trim();

    try {
        const res = await fetch('/api/departments', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description, lead_email })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
            closeCreateDeptModal();
            document.getElementById('modal-dept-name').value = '';
            document.getElementById('modal-dept-desc').value = '';
            document.getElementById('modal-dept-lead').value = '';
            await loadDepartments();
            loadAdminDepts();
        } else {
            showToast(data.message || 'Failed to create department', 'error');
        }
    } catch (err) {
        showToast('Network error creating department.', 'error');
    }
}

async function openEditDeptModal(deptId) {
    try {
        const res = await fetch(`/api/departments/${deptId}`);
        const data = await res.json();
        if (!res.ok || !data.department) {
            showToast(data.message || 'Department not found', 'error');
            return;
        }
        activeEditDept = data.department;
        document.getElementById('edit-dept-id').value = activeEditDept['Department ID'];
        document.getElementById('edit-dept-id-display').value = activeEditDept['Department ID'];
        document.getElementById('edit-dept-name').value = activeEditDept.Name || '';
        document.getElementById('edit-dept-desc').value = activeEditDept.Description || '';
        document.getElementById('edit-dept-lead').value = activeEditDept['Lead Email'] || '';
        document.getElementById('edit-dept-status').value = String(activeEditDept['Is Active']).toUpperCase() === 'FALSE' ? 'FALSE' : 'TRUE';
        document.getElementById('edit-dept-modal').style.display = 'flex';
    } catch (e) {
        showToast('Error loading department details.', 'error');
    }
}

function closeEditDeptModal() {
    document.getElementById('edit-dept-modal').style.display = 'none';
    activeEditDept = null;
    syncBodyModalOpen();
}

async function submitEditDept(e) {
    e.preventDefault();
    if (!activeEditDept) return;

    const deptId = document.getElementById('edit-dept-id').value;
    const name = document.getElementById('edit-dept-name').value.trim();
    const description = document.getElementById('edit-dept-desc').value.trim();
    const lead_email = document.getElementById('edit-dept-lead').value.trim();
    const is_active = document.getElementById('edit-dept-status').value;

    const btn = document.getElementById('edit-dept-submit-btn');
    btn.disabled = true;
    btn.textContent = 'Saving...';

    try {
        const res = await fetch(`/api/departments/${deptId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description, lead_email, is_active })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || 'Department updated successfully.', 'success');
            closeEditDeptModal();
            await loadDepartments();
            loadAdminDepts();
            // If department was renamed, refresh complaints in cache & views
            if (activeEditDept.Name !== name) {
                if (window.location.hash.includes('manage')) loadManageComplaints();
                if (window.location.hash.includes('my')) loadMyComplaints();
                if (window.location.hash.includes('dashboard')) loadDashboardData();
            }
        } else {
            showToast(data.message || 'Failed to update department', 'error');
        }
    } catch (err) {
        showToast('Network error updating department.', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Save Changes';
    }
}

async function openDeleteDeptModal(deptId) {
    activeDeleteDeptId = deptId;
    const modal = document.getElementById('delete-dept-modal');
    modal.style.display = 'flex';

    document.getElementById('delete-dept-id').value = deptId;
    document.getElementById('delete-dept-code-display').textContent = deptId;
    document.getElementById('delete-dept-name-display').textContent = 'Loading...';
    document.getElementById('delete-dept-usage-loading').style.display = 'block';
    document.getElementById('delete-dept-warning-linked').style.display = 'none';
    document.getElementById('delete-dept-clean').style.display = 'none';

    const confirmBtn = document.getElementById('delete-dept-confirm-btn');
    confirmBtn.disabled = true;
    confirmBtn.textContent = 'Checking Usage...';

    try {
        const res = await fetch(`/api/departments/${deptId}/usage`);
        const data = await res.json();
        document.getElementById('delete-dept-usage-loading').style.display = 'none';

        if (!res.ok) {
            showToast(data.message || 'Failed to check department usage', 'error');
            closeDeleteDeptModal();
            return;
        }

        activeDeleteDeptName = data.department_name || '';
        document.getElementById('delete-dept-name-display').textContent = activeDeleteDeptName;
        activeDeleteLinkedCount = data.usage_count || 0;

        if (activeDeleteLinkedCount > 0) {
            // Department has linked complaints!
            document.getElementById('delete-dept-warning-linked').style.display = 'block';
            document.getElementById('delete-dept-linked-count').textContent = activeDeleteLinkedCount;

            // Populate replacement select with other active departments
            const selectEl = document.getElementById('delete-dept-reassign-select');
            selectEl.innerHTML = '<option value="">Select Replacement Department...</option>';
            currentDepartments.forEach(d => {
                if (d['Department ID'] !== deptId && d.Name !== activeDeleteDeptName) {
                    const opt = document.createElement('option');
                    opt.value = d.Name;
                    opt.textContent = d.Name;
                    selectEl.appendChild(opt);
                }
            });
            confirmBtn.disabled = false;
            confirmBtn.textContent = `Reassign & Delete (${activeDeleteLinkedCount} complaints)`;
        } else {
            // Safe to delete directly
            document.getElementById('delete-dept-clean').style.display = 'block';
            confirmBtn.disabled = false;
            confirmBtn.textContent = 'Delete Department';
        }
    } catch (err) {
        document.getElementById('delete-dept-usage-loading').style.display = 'none';
        showToast('Network error checking department usage.', 'error');
        closeDeleteDeptModal();
    }
}

function closeDeleteDeptModal() {
    document.getElementById('delete-dept-modal').style.display = 'none';
    activeDeleteDeptId = null;
    activeDeleteDeptName = '';
    activeDeleteLinkedCount = 0;
    syncBodyModalOpen();
}

async function executeDeleteDept() {
    if (!activeDeleteDeptId) return;

    let reassign_to = null;
    if (activeDeleteLinkedCount > 0) {
        reassign_to = document.getElementById('delete-dept-reassign-select').value.trim();
        if (!reassign_to) {
            showToast('Please select a replacement department to reassign existing complaints.', 'error');
            return;
        }
    }

    const confirmBtn = document.getElementById('delete-dept-confirm-btn');
    confirmBtn.disabled = true;
    confirmBtn.textContent = 'Deleting from Google Sheets...';

    try {
        const res = await fetch(`/api/departments/${activeDeleteDeptId}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reassign_to: reassign_to })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || 'Department deleted successfully.', 'success');
            closeDeleteDeptModal();
            await loadDepartments();
            loadAdminDepts();
            // If complaints were reassigned, refresh complaints in views
            if (activeDeleteLinkedCount > 0) {
                if (window.location.hash.includes('manage')) loadManageComplaints();
                if (window.location.hash.includes('my')) loadMyComplaints();
                if (window.location.hash.includes('dashboard')) loadDashboardData();
            }
        } else {
            showToast(data.message || 'Failed to delete department', 'error');
            confirmBtn.disabled = false;
            confirmBtn.textContent = activeDeleteLinkedCount > 0 ? `Reassign & Delete (${activeDeleteLinkedCount} complaints)` : 'Delete Department';
        }
    } catch (err) {
        showToast('Network error deleting department.', 'error');
        confirmBtn.disabled = false;
        confirmBtn.textContent = activeDeleteLinkedCount > 0 ? `Reassign & Delete (${activeDeleteLinkedCount} complaints)` : 'Delete Department';
    }
}

// =============================================================================
// UTILITIES & TOASTS
// =============================================================================

function showToast(msg, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span>${escapeHtml(msg)}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 250);
    }, 4500);
}

function escapeHtml(text) {
    if (!text) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// =============================================================================
// REPORTS & ANALYTICS MODULE
// =============================================================================

let reportsState = {
    preset: 'all',
    filters: {
        date_preset: 'all',
        start_date: '',
        end_date: '',
        departments: [],
        vendors: [],
        statuses: [],
        priorities: [],
        handled_by: '',
        employee_name: '',
        salesperson_name: '',
        customer_name: '',
        complain_types: '',
        category: '',
        gr_amount_min: '',
        gr_amount_max: '',
        overdue_only: false,
        search: ''
    },
    selectedColumns: [
        'Complaint ID', 'Complain Date', 'Customer Name', 'Department Name',
        'Complain Types', 'Vendor Name', 'Priority', 'Status', 'Invoice No.',
        'Handled By', 'Total Time Taken', 'GR Amount'
    ],
    availableColumns: [],
    sortBy: 'Complain Date',
    sortDir: 'desc',
    groupBy: 'none',
    page: 1,
    pageSize: 25,
    totalRecords: 0,
    totalPages: 1,
    data: [],
    aggregations: null
};

async function initReportsView() {
    // 1. Populate multi-select department filter
    populateReportDepartmentFilter();

    // 2. Populate vendor filter options from cache
    populateReportVendorFilter();

    // 3. Load saved configuration if exists
    loadSavedReportConfiguration();

    // 4. Fetch initial report data
    await loadReportsData();
}

function populateReportDepartmentFilter() {
    const container = document.getElementById('report-dept-options');
    if (!container) return;

    if (!currentDepartments.length) {
        container.innerHTML = '<div class="text-muted p-2" style="font-size: 0.85rem;">No departments configured.</div>';
        return;
    }

    container.innerHTML = currentDepartments.map(d => `
        <label class="multi-select-option" data-name="${escapeHtml(d.Name).toLowerCase()}">
            <input type="checkbox" value="${escapeHtml(d.Name)}" onchange="onReportDeptCheckboxChange()">
            <span class="option-label">${escapeHtml(d.Name)}</span>
        </label>
    `).join('');

    updateReportDeptFilterLabel([]);
}

function onReportDeptCheckboxChange() {
    const container = document.getElementById('report-dept-options');
    if (!container) return;
    const checked = Array.from(container.querySelectorAll('input[type="checkbox"]:checked')).map(cb => cb.value.trim());
    reportsState.filters.departments = checked;
    updateReportDeptFilterLabel(checked);
}

function updateReportDeptFilterLabel(selected) {
    const labelEl = document.getElementById('report-dept-label');
    if (!labelEl) return;
    if (!selected || selected.length === 0) {
        labelEl.textContent = 'All Departments';
    } else if (selected.length === 1) {
        labelEl.innerHTML = `<span class="multi-select-chip">${escapeHtml(selected[0])}</span>`;
    } else if (selected.length <= 2) {
        labelEl.innerHTML = selected.map(s => `<span class="multi-select-chip">${escapeHtml(s)}</span>`).join('');
    } else {
        labelEl.innerHTML = `<span class="multi-select-chip">${escapeHtml(selected[0])}</span> <span class="multi-select-chip">+${selected.length - 1} more</span>`;
    }
}

function populateReportVendorFilter() {
    const container = document.getElementById('report-vendor-options');
    if (!container) return;

    // Extract unique vendors from allComplaintsCache
    const vendorSet = new Set();
    allComplaintsCache.forEach(c => {
        const v = (c['Vendor Name'] || '').trim();
        if (v) vendorSet.add(v);
    });

    const vendors = Array.from(vendorSet).sort();
    if (!vendors.length) {
        container.innerHTML = '<div class="text-muted p-2" style="font-size: 0.85rem;">No vendors recorded yet.</div>';
        return;
    }

    container.innerHTML = vendors.map(v => `
        <label class="multi-select-option" data-name="${escapeHtml(v).toLowerCase()}">
            <input type="checkbox" value="${escapeHtml(v)}" onchange="onReportVendorCheckboxChange()">
            <span class="option-label">${escapeHtml(v)}</span>
        </label>
    `).join('');

    updateReportVendorFilterLabel([]);
}

function onReportVendorCheckboxChange() {
    const container = document.getElementById('report-vendor-options');
    if (!container) return;
    const checked = Array.from(container.querySelectorAll('input[type="checkbox"]:checked')).map(cb => cb.value.trim());
    reportsState.filters.vendors = checked;
    updateReportVendorFilterLabel(checked);
}

function updateReportVendorFilterLabel(selected) {
    const labelEl = document.getElementById('report-vendor-label');
    if (!labelEl) return;
    if (!selected || selected.length === 0) {
        labelEl.textContent = 'All Vendors';
    } else if (selected.length === 1) {
        labelEl.innerHTML = `<span class="multi-select-chip">${escapeHtml(selected[0])}</span>`;
    } else {
        labelEl.innerHTML = `<span class="multi-select-chip">${escapeHtml(selected[0])}</span> <span class="multi-select-chip">+${selected.length - 1} more</span>`;
    }
}

function selectAllVendors() {
    const container = document.getElementById('report-vendor-options');
    if (!container) return;
    container.querySelectorAll('input[type="checkbox"]').forEach(cb => { cb.checked = true; });
    onReportVendorCheckboxChange();
}

function clearAllVendors() {
    const container = document.getElementById('report-vendor-options');
    if (!container) return;
    container.querySelectorAll('input[type="checkbox"]').forEach(cb => { cb.checked = false; });
    onReportVendorCheckboxChange();
}

function filterVendorOptions() {
    const q = (document.getElementById('report-vendor-search')?.value || '').toLowerCase().trim();
    const options = document.querySelectorAll('#report-vendor-options .multi-select-option');
    options.forEach(opt => {
        const name = opt.getAttribute('data-name') || '';
        opt.style.display = (!q || name.includes(q)) ? 'flex' : 'none';
    });
}

// =============================================================================
// REPORT DATA LOADING & API CALLS
// =============================================================================

async function loadReportsData() {
    const tbody = document.getElementById('report-table-body');
    if (tbody) {
        tbody.innerHTML = '<tr><td colspan="15" class="text-center py-4 text-muted"><span class="status-dot animate-pulse"></span> Fetching filtered records from Google Sheets...</td></tr>';
    }

    // Build URL query parameters
    const params = new URLSearchParams();
    const f = reportsState.filters;

    if (f.date_preset && f.date_preset !== 'all') params.append('date_preset', f.date_preset);
    if (f.start_date) params.append('start_date', f.start_date);
    if (f.end_date) params.append('end_date', f.end_date);
    if (f.departments && f.departments.length) params.append('departments', f.departments.join(','));
    if (f.vendors && f.vendors.length) params.append('vendors', f.vendors.join(','));
    if (f.statuses && f.statuses.length) params.append('statuses', f.statuses.join(','));
    if (f.priorities && f.priorities.length) params.append('priorities', f.priorities.join(','));
    if (f.handled_by) params.append('handled_by', f.handled_by);
    if (f.employee_name) params.append('employee_name', f.employee_name);
    if (f.salesperson_name) params.append('salesperson_name', f.salesperson_name);
    if (f.customer_name) params.append('customer_name', f.customer_name);
    if (f.complain_types) params.append('complain_types', f.complain_types);
    if (f.category) params.append('category', f.category);
    if (f.gr_amount_min) params.append('gr_amount_min', f.gr_amount_min);
    if (f.gr_amount_max) params.append('gr_amount_max', f.gr_amount_max);
    if (f.overdue_only) params.append('overdue_only', 'true');
    if (f.search) params.append('search', f.search);

    params.append('sort_by', reportsState.sortBy);
    params.append('sort_dir', reportsState.sortDir);
    params.append('page', reportsState.page);
    params.append('page_size', reportsState.pageSize);

    try {
        const res = await fetch(`/api/reports?${params.toString()}`);
        const data = await res.json();

        if (!res.ok) {
            showToast(data.message || 'Error compiling reports', 'error');
            if (tbody) tbody.innerHTML = `<tr><td colspan="15" class="text-center py-4 text-danger">${escapeHtml(data.message || 'Failed to load report data')}</td></tr>`;
            return;
        }

        reportsState.data = data.records || [];
        reportsState.aggregations = data;
        reportsState.totalRecords = data.pagination?.total_records || 0;
        reportsState.totalPages = data.pagination?.total_pages || 1;

        if (data.available_columns && (!reportsState.availableColumns || !reportsState.availableColumns.length)) {
            reportsState.availableColumns = data.available_columns;
            initReportColumnPicker(data.available_columns);
        }

        // Render all UI components
        updateReportActiveFilterBadge();
        renderReportKpis(data.kpis);
        renderReportCharts(data);
        renderReportTable();
        renderReportPagination(data.pagination);

    } catch (err) {
        console.error("Error loading report data:", err);
        if (tbody) tbody.innerHTML = '<tr><td colspan="15" class="text-center py-4 text-danger">Network error connecting to reports service.</td></tr>';
    }
}

function updateReportActiveFilterBadge() {
    const badge = document.getElementById('report-active-filter-badge');
    if (!badge) return;

    const parts = [];
    const f = reportsState.filters;

    if (f.date_preset && f.date_preset !== 'all') {
        parts.push(`Date: ${f.date_preset.replace('_', ' ').toUpperCase()}`);
    } else if (f.start_date || f.end_date) {
        parts.push(`Date: ${f.start_date || 'Start'} to ${f.end_date || 'Today'}`);
    }
    if (f.departments && f.departments.length) {
        parts.push(`Dept (${f.departments.length})`);
    }
    if (f.vendors && f.vendors.length) {
        parts.push(`Vendors (${f.vendors.length})`);
    }
    if (f.statuses && f.statuses.length) {
        parts.push(`Status: ${f.statuses.join(', ')}`);
    }
    if (f.priorities && f.priorities.length) {
        parts.push(`Priority: ${f.priorities.join(', ')}`);
    }
    if (f.overdue_only) {
        parts.push('Overdue Only');
    }
    if (f.search) {
        parts.push(`Search: "${f.search}"`);
    }

    if (parts.length > 0) {
        badge.textContent = `Active Filters: ${parts.join(' | ')}`;
        badge.style.color = '#1e3a8a';
        badge.style.fontWeight = '600';
    } else {
        badge.textContent = 'All Records (No Filters Active)';
        badge.style.color = 'var(--text-muted)';
        badge.style.fontWeight = 'normal';
    }
}

// =============================================================================
// KPI SUMMARY CARDS
// =============================================================================

function renderReportKpis(kpis) {
    if (!kpis) return;

    const totalEl = document.getElementById('report-kpi-total');
    const resEl = document.getElementById('report-kpi-resolved');
    const resRateEl = document.getElementById('report-kpi-res-rate');
    const pendEl = document.getElementById('report-kpi-pending');
    const avgTimeEl = document.getElementById('report-kpi-avg-time');
    const overdueEl = document.getElementById('report-kpi-overdue');
    const grTotalEl = document.getElementById('report-kpi-gr-total');
    const grPendingEl = document.getElementById('report-kpi-gr-pending');

    if (totalEl) totalEl.textContent = (kpis.total_complaints || 0).toLocaleString();
    if (resEl) resEl.textContent = (kpis.resolved_count || 0).toLocaleString();
    if (resRateEl) resRateEl.textContent = `${kpis.resolution_rate || 0}% resolution rate`;
    if (pendEl) pendEl.textContent = (kpis.pending_count || 0).toLocaleString();
    if (avgTimeEl) avgTimeEl.innerHTML = `${kpis.avg_resolution_time_days || 0} <span style="font-size: 1rem; font-weight: normal;">days</span>`;
    if (overdueEl) overdueEl.textContent = (kpis.overdue_count || 0).toLocaleString();

    const grVal = kpis.total_gr_amount || 0;
    const grPend = kpis.pending_gr_amount || 0;
    if (grTotalEl) grTotalEl.textContent = `₹${grVal.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    if (grPendingEl) grPendingEl.textContent = `Pending: ₹${grPend.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

// =============================================================================
// SELF-CONTAINED SVG ANALYTICS CHARTS (OFFLINE LAN CAPABLE)
// =============================================================================

function renderReportCharts(data) {
    renderDepartmentChart('chart-department-container', data.department_breakdown || []);
    renderMonthlyTrendChart('chart-trend-container', data.monthly_trend || []);
    renderStatusChart('chart-status-container', data.status_breakdown || {}, data.priority_breakdown || {});
    renderVendorChart('chart-vendor-container', data.top_vendors || []);
}

/**
 * Chart 1: Department Volume & Percentage (Horizontal Bar Chart)
 */
function renderDepartmentChart(containerId, depts) {
    const el = document.getElementById(containerId);
    if (!el) return;

    if (!depts || !depts.length) {
        el.innerHTML = '<div class="text-muted p-4 text-center">No department data for current filter.</div>';
        return;
    }

    const maxCount = Math.max(...depts.map(d => d.count), 1);
    const chartHeight = Math.max(220, depts.length * 36 + 20);

    let rowsHtml = depts.map(d => {
        const pct = Math.round((d.count / maxCount) * 100);
        return `
            <div style="display: flex; align-items: center; margin-bottom: 10px; font-size: 0.82rem;">
                <div style="width: 140px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 500;" title="${escapeHtml(d.department)}">${escapeHtml(d.department)}</div>
                <div style="flex: 1; margin: 0 12px; background: #f1f5f9; height: 18px; border-radius: 4px; overflow: hidden; position: relative;">
                    <div style="width: ${pct}%; background: linear-gradient(90deg, #3b82f6, #1d4ed8); height: 100%; border-radius: 4px; transition: width 0.4s ease;"></div>
                </div>
                <div style="width: 75px; text-align: right; font-weight: 600; color: #1e3a8a;">
                    ${d.count} <span style="font-weight: normal; font-size: 0.75rem; color: var(--text-muted);">(${d.percentage}%)</span>
                </div>
            </div>
        `;
    }).join('');

    el.innerHTML = `<div style="padding: 14px; max-height: 280px; overflow-y: auto;">${rowsHtml}</div>`;
}

/**
 * Chart 2: Monthly Trend (SVG Area & Line Chart)
 */
function renderMonthlyTrendChart(containerId, trendData) {
    const el = document.getElementById(containerId);
    if (!el) return;

    if (!trendData || !trendData.length) {
        el.innerHTML = '<div class="text-muted p-4 text-center">No monthly trend data available.</div>';
        return;
    }

    const width = 420;
    const height = 200;
    const padding = { top: 20, right: 25, bottom: 35, left: 35 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    const maxVal = Math.max(...trendData.map(d => Math.max(d.lodged, d.resolved)), 5);

    const getX = (idx) => padding.left + (idx / Math.max(1, trendData.length - 1)) * chartW;
    const getY = (val) => padding.top + chartH - (val / maxVal) * chartH;

    // Build lodged points and path
    const lodgedPoints = trendData.map((d, i) => `${getX(i)},${getY(d.lodged)}`).join(' ');
    const resolvedPoints = trendData.map((d, i) => `${getX(i)},${getY(d.resolved)}`).join(' ');

    // Horizontal grid lines
    const gridLines = [0, 0.5, 1].map(frac => {
        const y = padding.top + chartH * (1 - frac);
        const labelVal = Math.round(maxVal * frac);
        return `
            <line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" stroke="#e2e8f0" stroke-dasharray="3,3" />
            <text x="${padding.left - 6}" y="${y + 4}" fill="#94a3b8" font-size="9" text-anchor="end">${labelVal}</text>
        `;
    }).join('');

    // X Axis Month Labels
    const xLabels = trendData.map((d, i) => {
        const x = getX(i);
        const label = d.month.length > 7 ? d.month.slice(5) : d.month;
        return `<text x="${x}" y="${height - 10}" fill="#64748b" font-size="9" text-anchor="middle">${label}</text>`;
    }).join('');

    // Dots
    const lodgedDots = trendData.map((d, i) => `
        <circle cx="${getX(i)}" cy="${getY(d.lodged)}" r="3.5" fill="#2563eb" stroke="#ffffff" stroke-width="1.5">
            <title>${d.month}: ${d.lodged} Lodged</title>
        </circle>
    `).join('');

    const resolvedDots = trendData.map((d, i) => `
        <circle cx="${getX(i)}" cy="${getY(d.resolved)}" r="3.5" fill="#10b981" stroke="#ffffff" stroke-width="1.5">
            <title>${d.month}: ${d.resolved} Resolved</title>
        </circle>
    `).join('');

    el.innerHTML = `
        <div style="padding: 10px;">
            <div style="display: flex; gap: 16px; justify-content: flex-end; margin-bottom: 6px; font-size: 0.75rem;">
                <span style="display: flex; align-items: center; gap: 4px; color: #2563eb; font-weight: 600;">
                    <span style="display: inline-block; width: 10px; height: 10px; background: #2563eb; border-radius: 2px;"></span> Lodged
                </span>
                <span style="display: flex; align-items: center; gap: 4px; color: #10b981; font-weight: 600;">
                    <span style="display: inline-block; width: 10px; height: 10px; background: #10b981; border-radius: 2px;"></span> Resolved
                </span>
            </div>
            <svg viewBox="0 0 ${width} ${height}" class="chart-svg">
                ${gridLines}
                <polyline fill="none" stroke="#2563eb" stroke-width="2.5" points="${lodgedPoints}" />
                <polyline fill="none" stroke="#10b981" stroke-width="2.5" stroke-dasharray="4,2" points="${resolvedPoints}" />
                ${lodgedDots}
                ${resolvedDots}
                ${xLabels}
            </svg>
        </div>
    `;
}

/**
 * Chart 3: Status & Priority Distribution (Segmented Donut + Priority Bars)
 */
function renderStatusChart(containerId, statusCounts, prioCounts) {
    const el = document.getElementById(containerId);
    if (!el) return;

    const statuses = [
        { key: 'Submitted', color: '#3b82f6' },
        { key: 'Under Review', color: '#f59e0b' },
        { key: 'In Progress', color: '#6366f1' },
        { key: 'Resolved', color: '#10b981' },
        { key: 'Closed', color: '#64748b' },
        { key: 'Rejected', color: '#ef4444' }
    ];

    const total = Object.values(statusCounts).reduce((a, b) => a + b, 0);
    if (total === 0) {
        el.innerHTML = '<div class="text-muted p-4 text-center">No status data available.</div>';
        return;
    }

    // Status items list with colored pills
    const statusPillsHtml = statuses.map(s => {
        const count = statusCounts[s.key] || 0;
        const pct = Math.round((count / total) * 100);
        return `
            <div style="display: flex; justify-content: space-between; align-items: center; padding: 4px 0; font-size: 0.8rem; border-bottom: 1px solid #f1f5f9;">
                <span style="display: flex; align-items: center; gap: 6px;">
                    <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: ${s.color};"></span>
                    ${s.key}
                </span>
                <span style="font-weight: 600;">${count} <small class="text-muted">(${pct}%)</small></span>
            </div>
        `;
    }).join('');

    // Priority badges
    const prios = [
        { key: 'Urgent', color: '#dc2626' },
        { key: 'High', color: '#f97316' },
        { key: 'Medium', color: '#3b82f6' },
        { key: 'Low', color: '#64748b' }
    ];

    const prioBarsHtml = prios.map(p => {
        const count = prioCounts[p.key] || 0;
        const pct = Math.round((count / Math.max(1, total)) * 100);
        return `
            <div style="display: flex; align-items: center; margin-bottom: 6px; font-size: 0.78rem;">
                <span style="width: 55px; font-weight: 500;">${p.key}</span>
                <div style="flex: 1; height: 8px; background: #f1f5f9; border-radius: 4px; overflow: hidden; margin: 0 8px;">
                    <div style="width: ${pct}%; background: ${p.color}; height: 100%;"></div>
                </div>
                <span style="width: 30px; text-align: right; font-weight: 600;">${count}</span>
            </div>
        `;
    }).join('');

    el.innerHTML = `
        <div style="padding: 12px; display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
            <div>
                <div style="font-size: 0.8rem; font-weight: 600; color: #475569; margin-bottom: 8px;">Status Breakdown</div>
                ${statusPillsHtml}
            </div>
            <div>
                <div style="font-size: 0.8rem; font-weight: 600; color: #475569; margin-bottom: 8px;">Priority Severity</div>
                ${prioBarsHtml}
            </div>
        </div>
    `;
}

/**
 * Chart 4: Top Vendors by Volume & Goods Return Amount (Bar & Financial Exposure)
 */
function renderVendorChart(containerId, topVendors) {
    const el = document.getElementById(containerId);
    if (!el) return;

    if (!topVendors || !topVendors.length) {
        el.innerHTML = '<div class="text-muted p-4 text-center">No vendor complaint data recorded.</div>';
        return;
    }

    const maxCount = Math.max(...topVendors.map(v => v.count), 1);

    const rowsHtml = topVendors.slice(0, 5).map(v => {
        const pct = Math.round((v.count / maxCount) * 100);
        const grFormatted = v.gr_amount > 0 ? `₹${v.gr_amount.toLocaleString('en-IN')}` : '-';
        return `
            <div style="margin-bottom: 10px; font-size: 0.82rem;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 3px;">
                    <span style="font-weight: 600; color: #1e293b;">${escapeHtml(v.vendor)}</span>
                    <span style="font-weight: 600; color: #059669;">GR: ${grFormatted}</span>
                </div>
                <div style="display: flex; align-items: center; gap: 8px;">
                    <div style="flex: 1; background: #f1f5f9; height: 14px; border-radius: 4px; overflow: hidden;">
                        <div style="width: ${pct}%; background: #0284c7; height: 100%; border-radius: 4px;"></div>
                    </div>
                    <span style="font-size: 0.78rem; font-weight: 600; width: 45px; text-align: right;">${v.count} iss.</span>
                </div>
            </div>
        `;
    }).join('');

    el.innerHTML = `<div style="padding: 14px; max-height: 280px; overflow-y: auto;">${rowsHtml}</div>`;
}

// =============================================================================
// REPORT DATA TABLE RENDERING
// =============================================================================

function renderReportTable() {
    const thead = document.getElementById('report-table-head');
    const tbody = document.getElementById('report-table-body');
    const tfoot = document.getElementById('report-table-foot');
    if (!thead || !tbody) return;

    const cols = reportsState.selectedColumns;
    const records = reportsState.data || [];

    // 1. Table Headers with Sortable triggers
    thead.innerHTML = `
        <tr>
            ${cols.map(cId => {
                const isSorted = reportsState.sortBy === cId;
                const sortIcon = isSorted ? (reportsState.sortDir === 'asc' ? '▲' : '▼') : '';
                return `
                    <th class="sortable-th" onclick="handleReportSort('${escapeHtml(cId)}')" title="Click to sort by ${escapeHtml(cId)}">
                        <span>${escapeHtml(cId)}</span>
                        <span class="sort-icon">${sortIcon}</span>
                    </th>
                `;
            }).join('')}
            <th style="width: 80px; text-align: center;">Action</th>
        </tr>
    `;

    // 2. Data Rows (with grouping if active)
    if (!records.length) {
        tbody.innerHTML = `<tr><td colspan="${cols.length + 1}" class="text-center py-4 text-muted">No complaints match the specified criteria. Try clearing or expanding your filters.</td></tr>`;
        if (tfoot) tfoot.innerHTML = '';
        return;
    }

    const groupBy = reportsState.groupBy;
    let bodyHtml = '';
    let totalGrSum = 0;

    if (groupBy && groupBy !== 'none') {
        // Group records by the chosen field
        const groups = {};
        records.forEach(r => {
            let key = r[groupBy] || 'Unspecified';
            if (groupBy === 'Complain Date' && key) {
                key = key.slice(0, 7); // group by YYYY-MM
            }
            if (!groups[key]) groups[key] = [];
            groups[key].push(r);
        });

        Object.keys(groups).sort().forEach(groupKey => {
            const groupRecords = groups[groupKey];
            bodyHtml += `
                <tr class="table-group-header">
                    <td colspan="${cols.length + 1}">
                        <span>${escapeHtml(groupBy)}: <strong>${escapeHtml(groupKey)}</strong></span>
                        <span class="table-group-badge">${groupRecords.length} record${groupRecords.length !== 1 ? 's' : ''}</span>
                    </td>
                </tr>
            `;
            groupRecords.forEach(rec => {
                bodyHtml += renderReportTableRow(rec, cols);
                totalGrSum += parseFloat(rec['GR Amount'] || 0) || 0;
            });
        });
    } else {
        records.forEach(rec => {
            bodyHtml += renderReportTableRow(rec, cols);
            totalGrSum += parseFloat(rec['GR Amount'] || 0) || 0;
        });
    }

    tbody.innerHTML = bodyHtml;

    // 3. Totals Summary Footer Row
    if (tfoot) {
        tfoot.innerHTML = `
            <tr>
                <td colspan="${Math.max(1, cols.length - 2)}">
                    <strong>Page Total: ${records.length} record(s) &nbsp;|&nbsp; Overall Matching: ${reportsState.totalRecords} record(s)</strong>
                </td>
                <td colspan="3" style="text-align: right; color: #059669; font-weight: 700;">
                    ${cols.includes('GR Amount') ? `Sum GR Amount: ₹${totalGrSum.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : ''}
                </td>
            </tr>
        `;
    }
}

function renderReportTableRow(c, cols) {
    const canEditAny = hasPermission('complaints:edit');
    const isOwner = currentUser && (c['Complainant Email'] || '').toLowerCase() === (currentUser.email || '').toLowerCase();
    const isStaffOrAdmin = ['Super Admin', 'Admin', 'Department Head', 'Department Staff'].includes(currentUser?.role);
    const canEdit = canEditAny && (isStaffOrAdmin || (isOwner && (c.Status || '').toLowerCase() === 'submitted'));

    const cellsHtml = cols.map(cId => {
        const val = c[cId] || '-';
        if (cId === 'Status') {
            const slug = String(val).toLowerCase().replace(/\s+/g, '-');
            return `<td><span class="badge badge-status-${slug}">${escapeHtml(val)}</span></td>`;
        }
        if (cId === 'Priority') {
            const slug = String(val).toLowerCase();
            return `<td><span class="badge badge-prio-${slug}">${escapeHtml(val)}</span></td>`;
        }
        if (cId === 'Department Name') {
            return `<td>${formatDepartmentBadges(val)}</td>`;
        }
        if (cId === 'GR Amount') {
            const num = parseFloat(val) || 0;
            return `<td style="text-align: right; font-weight: 600; color: #059669;">${num > 0 ? `₹${num.toLocaleString('en-IN', { minimumFractionDigits: 2 })}` : '-'}</td>`;
        }
        if (cId === 'Complaint ID') {
            return `<td><strong><code>${escapeHtml(val)}</code></strong></td>`;
        }
        if (cId === 'Customer Name') {
            return `<td><strong>${escapeHtml(val)}</strong></td>`;
        }
        return `<td>${escapeHtml(val)}</td>`;
    }).join('');

    return `
        <tr>
            ${cellsHtml}
            <td style="text-align: center;">
                <div class="table-row-actions" style="justify-content: center;">
                    <button class="btn btn-secondary btn-sm" onclick="openComplaintModal('${escapeHtml(c['Complaint ID'])}')">View</button>
                    ${canEdit ? `<button class="btn btn-warning btn-sm" onclick="openEditComplaintModal('${escapeHtml(c['Complaint ID'])}')">Edit</button>` : ''}
                </div>
            </td>
        </tr>
    `;
}

// =============================================================================
// REPORT PAGINATION CONTROLS
// =============================================================================

function renderReportPagination(p) {
    const infoEl = document.getElementById('report-pagination-info');
    const controlsEl = document.getElementById('report-pagination-controls');
    if (!infoEl || !controlsEl) return;

    if (!p || p.total_records === 0) {
        infoEl.textContent = 'Showing 0 to 0 of 0 complaints';
        controlsEl.innerHTML = '';
        return;
    }

    const start = (p.page - 1) * (parseInt(p.page_size) || p.total_records) + 1;
    const end = Math.min(p.total_records, start + (reportsState.data?.length || 0) - 1);
    infoEl.textContent = `Showing ${start} to ${end} of ${p.total_records} complaints`;

    if (p.total_pages <= 1) {
        controlsEl.innerHTML = '';
        return;
    }

    let buttonsHtml = '';
    // Previous
    buttonsHtml += `<button type="button" class="btn btn-secondary btn-sm" ${p.page <= 1 ? 'disabled' : ''} onclick="goToReportPage(${p.page - 1})">&larr; Prev</button>`;

    // Page numbers
    const cur = p.page;
    const total = p.total_pages;
    const range = [];

    for (let i = 1; i <= total; i++) {
        if (i === 1 || i === total || (i >= cur - 1 && i <= cur + 1)) {
            range.push(i);
        } else if (range[range.length - 1] !== '...') {
            range.push('...');
        }
    }

    range.forEach(item => {
        if (item === '...') {
            buttonsHtml += `<span class="pagination-ellipsis" style="padding: 4px 6px; color: var(--text-muted);">...</span>`;
        } else {
            const isActive = (item === cur);
            buttonsHtml += `<button type="button" class="btn ${isActive ? 'btn-primary' : 'btn-secondary'} btn-sm" onclick="goToReportPage(${item})">${item}</button>`;
        }
    });

    // Next
    buttonsHtml += `<button type="button" class="btn btn-secondary btn-sm" ${p.page >= total ? 'disabled' : ''} onclick="goToReportPage(${p.page + 1})">Next &rarr;</button>`;

    controlsEl.innerHTML = buttonsHtml;
}

function goToReportPage(page) {
    if (page < 1 || page > reportsState.totalPages) return;
    reportsState.page = page;
    loadReportsData();
}

function handleReportPageSizeChange() {
    const el = document.getElementById('report-page-size');
    if (el) {
        reportsState.pageSize = el.value;
        reportsState.page = 1;
        loadReportsData();
    }
}

function handleReportGroupByChange() {
    const el = document.getElementById('report-group-by');
    if (el) {
        reportsState.groupBy = el.value;
        renderReportTable();
    }
}

function handleReportSort(colId) {
    if (reportsState.sortBy === colId) {
        reportsState.sortDir = reportsState.sortDir === 'asc' ? 'desc' : 'asc';
    } else {
        reportsState.sortBy = colId;
        reportsState.sortDir = 'asc';
    }
    loadReportsData();
}

// =============================================================================
// READY-MADE REPORT PRESETS
// =============================================================================

function applyReportPreset(preset) {
    reportsState.preset = preset;

    // Update active preset button highlight
    document.querySelectorAll('#report-preset-buttons .preset-btn').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-preset') === preset);
    });

    // Configure preset defaults
    if (preset === 'all') {
        reportsState.groupBy = 'none';
        reportsState.sortBy = 'Complain Date';
        reportsState.sortDir = 'desc';
        reportsState.filters.overdue_only = false;
        reportsState.selectedColumns = [
            'Complaint ID', 'Complain Date', 'Customer Name', 'Department Name',
            'Complain Types', 'Vendor Name', 'Priority', 'Status', 'Invoice No.',
            'Handled By', 'Total Time Taken', 'GR Amount'
        ];
    } else if (preset === 'department') {
        reportsState.groupBy = 'Department Name';
        reportsState.sortBy = 'Department Name';
        reportsState.sortDir = 'asc';
        reportsState.selectedColumns = [
            'Complaint ID', 'Complain Date', 'Customer Name', 'Department Name',
            'Priority', 'Status', 'Handled By', 'Total Time Taken', 'GR Amount'
        ];
    } else if (preset === 'vendor') {
        reportsState.groupBy = 'Vendor Name';
        reportsState.sortBy = 'Vendor Name';
        reportsState.sortDir = 'asc';
        reportsState.selectedColumns = [
            'Complaint ID', 'Complain Date', 'Customer Name', 'Vendor Name',
            'Category', 'Invoice No.', 'Status', 'GR Amount', 'GR Expected Date'
        ];
    } else if (preset === 'employee') {
        reportsState.groupBy = 'Handled By';
        reportsState.sortBy = 'Handled By';
        reportsState.sortDir = 'asc';
        reportsState.selectedColumns = [
            'Complaint ID', 'Complain Date', 'Handled By', 'Department Name',
            'Complain Types', 'Status', 'Total Time Taken', 'Solution Given'
        ];
    } else if (preset === 'monthly') {
        reportsState.groupBy = 'Complain Date';
        reportsState.sortBy = 'Complain Date';
        reportsState.sortDir = 'desc';
        reportsState.selectedColumns = [
            'Complaint ID', 'Complain Date', 'Customer Name', 'Department Name',
            'Priority', 'Status', 'Closing Date', 'Total Time Taken'
        ];
    } else if (preset === 'status') {
        reportsState.groupBy = 'Status';
        reportsState.sortBy = 'Status';
        reportsState.sortDir = 'asc';
        reportsState.selectedColumns = [
            'Complaint ID', 'Complain Date', 'Customer Name', 'Department Name',
            'Priority', 'Status', 'Handled By', 'Closing Date'
        ];
    } else if (preset === 'priority') {
        reportsState.groupBy = 'Priority';
        reportsState.sortBy = 'Priority';
        reportsState.sortDir = 'asc';
        reportsState.selectedColumns = [
            'Complaint ID', 'Complain Date', 'Customer Name', 'Priority',
            'Category', 'Complain Types', 'Status', 'GR Amount'
        ];
    } else if (preset === 'customer') {
        reportsState.groupBy = 'none';
        reportsState.sortBy = 'Customer Name';
        reportsState.sortDir = 'asc';
        reportsState.selectedColumns = [
            'Complaint ID', 'Complain Date', 'Customer Name', 'Customer Mobile No.',
            'Invoice No.', 'Complain Types', 'Status', 'Handled By', 'GR Amount'
        ];
    } else if (preset === 'gr') {
        reportsState.groupBy = 'none';
        reportsState.sortBy = 'GR Amount';
        reportsState.sortDir = 'desc';
        reportsState.filters.gr_amount_min = '0.01';
        const grMinEl = document.getElementById('report-filter-gr-min');
        if (grMinEl) grMinEl.value = '0.01';
        reportsState.selectedColumns = [
            'Complaint ID', 'Complain Date', 'Customer Name', 'Vendor Name',
            'Invoice No.', 'Status', 'GR Amount', 'GR Expected Date'
        ];
    } else if (preset === 'overdue') {
        reportsState.groupBy = 'none';
        reportsState.sortBy = 'Complain Date';
        reportsState.sortDir = 'asc';
        reportsState.filters.overdue_only = true;
        const overdueEl = document.getElementById('report-filter-overdue');
        if (overdueEl) overdueEl.checked = true;
        reportsState.selectedColumns = [
            'Complaint ID', 'Complain Date', 'Customer Name', 'Department Name',
            'Priority', 'Status', 'Handled By', 'Total Time Taken', 'GR Expected Date'
        ];
    }

    // Sync Group By selector
    const groupSelect = document.getElementById('report-group-by');
    if (groupSelect) groupSelect.value = reportsState.groupBy;

    updateReportColumnCheckboxes();
    reportsState.page = 1;
    loadReportsData();
}

// =============================================================================
// FILTER BUILDER EVENT HANDLERS
// =============================================================================

function handleApplyReportFilters(e) {
    e.preventDefault();

    const f = reportsState.filters;
    f.date_preset = document.getElementById('report-filter-date-preset')?.value || 'all';
    f.start_date = document.getElementById('report-filter-start-date')?.value || '';
    f.end_date = document.getElementById('report-filter-end-date')?.value || '';
    f.search = (document.getElementById('report-filter-search')?.value || '').trim();

    const statusVal = document.getElementById('report-filter-status')?.value || '';
    f.statuses = statusVal ? [statusVal] : [];

    const prioVal = document.getElementById('report-filter-priority')?.value || '';
    f.priorities = prioVal ? [prioVal] : [];

    f.handled_by = (document.getElementById('report-filter-handled-by')?.value || '').trim();
    f.employee_name = (document.getElementById('report-filter-employee')?.value || '').trim();
    f.salesperson_name = (document.getElementById('report-filter-salesperson')?.value || '').trim();
    f.customer_name = (document.getElementById('report-filter-customer')?.value || '').trim();
    f.complain_types = (document.getElementById('report-filter-type')?.value || '').trim();
    f.category = (document.getElementById('report-filter-category')?.value || '').trim();

    f.gr_amount_min = (document.getElementById('report-filter-gr-min')?.value || '').trim();
    f.gr_amount_max = (document.getElementById('report-filter-gr-max')?.value || '').trim();
    f.overdue_only = document.getElementById('report-filter-overdue')?.checked || false;

    reportsState.page = 1;
    loadReportsData();
}

function resetReportFilters() {
    reportsState.filters = {
        date_preset: 'all',
        start_date: '',
        end_date: '',
        departments: [],
        vendors: [],
        statuses: [],
        priorities: [],
        handled_by: '',
        employee_name: '',
        salesperson_name: '',
        customer_name: '',
        complain_types: '',
        category: '',
        gr_amount_min: '',
        gr_amount_max: '',
        overdue_only: false,
        search: ''
    };

    const form = document.getElementById('report-filter-form');
    if (form) form.reset();

    clearAllDepartments('report-dept');
    clearAllVendors();

    reportsState.page = 1;
    loadReportsData();
}

function handleReportDatePresetChange() {
    const val = document.getElementById('report-filter-date-preset')?.value;
    const startEl = document.getElementById('report-filter-start-date');
    const endEl = document.getElementById('report-filter-end-date');

    if (val === 'custom') {
        if (startEl) startEl.focus();
    } else {
        if (startEl) startEl.value = '';
        if (endEl) endEl.value = '';
    }
}

function toggleReportFilters() {
    const panel = document.getElementById('report-filter-panel');
    const textEl = document.getElementById('btn-toggle-filters-text');
    if (!panel) return;

    if (panel.style.display === 'none') {
        panel.style.display = 'block';
        if (textEl) textEl.textContent = 'Hide Filter Builder';
    } else {
        panel.style.display = 'none';
        if (textEl) textEl.textContent = 'Show Filter Builder';
    }
}

// =============================================================================
// COLUMN CUSTOMIZER
// =============================================================================

function toggleColumnCustomizer() {
    const el = document.getElementById('report-column-picker');
    if (el) {
        el.style.display = el.style.display === 'none' ? 'block' : 'none';
    }
}

function initReportColumnPicker(columns) {
    const container = document.getElementById('report-column-checkboxes-container');
    if (!container) return;

    container.innerHTML = columns.map(col => {
        const checked = reportsState.selectedColumns.includes(col.id);
        return `
            <label class="column-customizer-item" title="${escapeHtml(col.category)}">
                <input type="checkbox" value="${escapeHtml(col.id)}" ${checked ? 'checked' : ''} onchange="onReportColumnCheckboxChange('${escapeHtml(col.id)}')">
                <span>${escapeHtml(col.label)}</span>
            </label>
        `;
    }).join('');

    updateReportColumnCountBadge();
}

function onReportColumnCheckboxChange(colId) {
    if (reportsState.selectedColumns.includes(colId)) {
        if (reportsState.selectedColumns.length <= 1) {
            showToast('At least one column must remain selected.', 'warning');
            updateReportColumnCheckboxes();
            return;
        }
        reportsState.selectedColumns = reportsState.selectedColumns.filter(c => c !== colId);
    } else {
        reportsState.selectedColumns.push(colId);
    }

    updateReportColumnCountBadge();
    renderReportTable();
}

function updateReportColumnCheckboxes() {
    const container = document.getElementById('report-column-checkboxes-container');
    if (!container) return;
    container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.checked = reportsState.selectedColumns.includes(cb.value);
    });
    updateReportColumnCountBadge();
}

function updateReportColumnCountBadge() {
    const badge = document.getElementById('report-column-count-badge');
    if (badge) badge.textContent = reportsState.selectedColumns.length;
}

function selectAllReportColumns() {
    if (!reportsState.availableColumns.length) return;
    reportsState.selectedColumns = reportsState.availableColumns.map(c => c.id);
    updateReportColumnCheckboxes();
    renderReportTable();
}

function resetDefaultReportColumns() {
    reportsState.selectedColumns = [
        'Complaint ID', 'Complain Date', 'Customer Name', 'Department Name',
        'Complain Types', 'Vendor Name', 'Priority', 'Status', 'Invoice No.',
        'Handled By', 'Total Time Taken', 'GR Amount'
    ];
    updateReportColumnCheckboxes();
    renderReportTable();
}

// Close column customizer on outer click
document.addEventListener('click', (e) => {
    if (!e.target.closest('.column-customizer-popover-container')) {
        const picker = document.getElementById('report-column-picker');
        if (picker) picker.style.display = 'none';
    }
});

// =============================================================================
// REPORT EXPORTS (EXCEL .xlsx, CSV, PDF, PRINTABLE)
// =============================================================================

async function exportReport(format) {
    showToast(`Generating ${format.toUpperCase()} report from Google Sheets...`, 'info');

    const payload = {
        filters: reportsState.filters,
        columns: reportsState.selectedColumns,
        sort_by: reportsState.sortBy,
        sort_dir: reportsState.sortDir,
        report_title: `Customer Complaint Management Report (${reportsState.preset.toUpperCase()})`
    };

    try {
        const res = await fetch(`/api/reports/export/${format}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            showToast(err.message || `Export failed: HTTP ${res.status}`, 'error');
            return;
        }

        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;

        const timestamp = new Date().toISOString().replace(/[-:T.]/g, '').slice(0, 14);
        const ext = format === 'excel' ? 'xlsx' : format;
        a.download = `complaints_report_${timestamp}.${ext}`;

        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        a.remove();

        showToast(`${format.toUpperCase()} report downloaded successfully!`, 'success');
    } catch (e) {
        console.error("Export error:", e);
        showToast(`Failed to export ${format.toUpperCase()}: Network error.`, 'error');
    }
}

function openPrintableReport() {
    const modal = document.getElementById('printable-report-modal');
    const content = document.getElementById('print-document-content');
    if (!modal || !content) return;

    const kpis = reportsState.aggregations?.kpis || {};
    const cols = reportsState.selectedColumns;
    const records = reportsState.data || [];
    const genDate = new Date().toLocaleString();

    let totalGr = 0;
    records.forEach(r => { totalGr += parseFloat(r['GR Amount'] || 0) || 0; });

    content.innerHTML = `
        <div class="print-report-container" style="padding: 10px; font-family: 'Inter', sans-serif;">
            <div class="print-report-header">
                <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                    <div>
                        <h2 style="margin: 0; color: #1e3a8a; font-size: 1.4rem;">CUSTOMER COMPLAINT MANAGEMENT SYSTEM</h2>
                        <h4 style="margin: 4px 0 0; color: #475569; font-weight: 500;">Comprehensive Analytical Report (${reportsState.preset.toUpperCase()})</h4>
                    </div>
                    <div style="text-align: right; font-size: 0.8rem; color: #64748b;">
                        <div><strong>Generated At:</strong> ${genDate}</div>
                        <div><strong>Generated By:</strong> ${escapeHtml(currentUser?.name || 'Authorized User')} (${escapeHtml(currentUser?.role || '-')})</div>
                    </div>
                </div>

                <div class="print-meta-grid">
                    <div><strong>Total Complaints:</strong> ${kpis.total_complaints || records.length}</div>
                    <div><strong>Resolved:</strong> ${kpis.resolved_count || 0} (${kpis.resolution_rate || 0}%)</div>
                    <div><strong>Pending:</strong> ${kpis.pending_count || 0}</div>
                    <div><strong>Overdue:</strong> ${kpis.overdue_count || 0}</div>
                    <div><strong>Avg Resolution:</strong> ${kpis.avg_resolution_time_days || 0} days</div>
                    <div><strong>Total GR Amount:</strong> ₹${(kpis.total_gr_amount || totalGr).toLocaleString('en-IN', { minimumFractionDigits: 2 })}</div>
                    <div style="grid-column: span 2;"><strong>Active Filters:</strong> ${escapeHtml(document.getElementById('report-active-filter-badge')?.textContent || 'All')}</div>
                </div>
            </div>

            <table class="data-table" style="width: 100%; border-collapse: collapse; margin-top: 14px; font-size: 0.8rem;">
                <thead>
                    <tr style="background: #1e3a8a; color: #ffffff;">
                        ${cols.map(c => `<th style="padding: 6px 8px; border: 1px solid #cbd5e1; text-align: left;">${escapeHtml(c)}</th>`).join('')}
                    </tr>
                </thead>
                <tbody>
                    ${records.map((r, i) => `
                        <tr style="background: ${i % 2 === 1 ? '#f8fafc' : '#ffffff'};">
                            ${cols.map(c => {
                                const val = r[c] || '-';
                                const isGr = c === 'GR Amount';
                                return `<td style="padding: 5px 8px; border: 1px solid #e2e8f0; text-align: ${isGr ? 'right' : 'left'};">${escapeHtml(val)}</td>`;
                            }).join('')}
                        </tr>
                    `).join('')}
                </tbody>
                <tfoot>
                    <tr style="background: #f1f5f9; font-weight: bold;">
                        <td colspan="${Math.max(1, cols.length - 1)}" style="padding: 6px 8px; border: 1px solid #cbd5e1;">
                            Total Records: ${records.length}
                        </td>
                        <td style="padding: 6px 8px; border: 1px solid #cbd5e1; text-align: right; color: #059669;">
                            ${cols.includes('GR Amount') ? `₹${totalGr.toLocaleString('en-IN', { minimumFractionDigits: 2 })}` : ''}
                        </td>
                    </tr>
                </tfoot>
            </table>
        </div>
    `;

    modal.style.display = 'flex';
}

function closePrintableReportModal() {
    const modal = document.getElementById('printable-report-modal');
    if (modal) modal.style.display = 'none';
    syncBodyModalOpen();
}

function executeBrowserPrint() {
    window.print();
}

// =============================================================================
// SAVED REPORT CONFIGURATIONS (PERSISTENCE)
// =============================================================================

function saveReportConfiguration() {
    const config = {
        filters: reportsState.filters,
        selectedColumns: reportsState.selectedColumns,
        groupBy: reportsState.groupBy,
        pageSize: reportsState.pageSize,
        preset: reportsState.preset
    };

    try {
        localStorage.setItem('cms_saved_report_config', JSON.stringify(config));
        showToast('Report view configuration saved successfully to your browser!', 'success');
    } catch (e) {
        showToast('Could not save configuration: LocalStorage disabled.', 'warning');
    }
}

function loadSavedReportConfiguration() {
    try {
        const raw = localStorage.getItem('cms_saved_report_config');
        if (!raw) return;
        const config = JSON.parse(raw);

        if (config.selectedColumns && Array.isArray(config.selectedColumns) && config.selectedColumns.length > 0) {
            reportsState.selectedColumns = config.selectedColumns;
        }
        if (config.groupBy) {
            reportsState.groupBy = config.groupBy;
            const el = document.getElementById('report-group-by');
            if (el) el.value = config.groupBy;
        }
        if (config.pageSize) {
            reportsState.pageSize = config.pageSize;
            const el = document.getElementById('report-page-size');
            if (el) el.value = config.pageSize;
        }
        if (config.preset) {
            reportsState.preset = config.preset;
        }
    } catch (e) {
        console.warn("Could not load saved report configuration:", e);
    }
}

// =============================================================================
// MASTER DATA & SEARCHABLE DROPDOWNS (Complaint Types, Salespersons, From Dept)
// =============================================================================

let masterOptionsCache = {
    complaint_types: [],
    complaint_types_detailed: [],
    salespersons: [],
    salespersons_detailed: [],
    departments: [],
    from_departments: ['Online', 'Offline', 'Packing', 'None']
};

let masterComplaintTypesList = [];
let masterSalespersonsList = [];
let activeSearchableInputId = null;

async function loadMasterOptions() {
    try {
        const res = await fetch('/api/master/options');
        const data = await res.json();
        if (res.ok && data.status === 'success') {
            masterOptionsCache = {
                complaint_types: data.complaint_types || [],
                complaint_types_detailed: data.complaint_types_detailed || [],
                salespersons: data.salespersons || [],
                salespersons_detailed: data.salespersons_detailed || [],
                departments: data.departments || [],
                from_departments: data.from_departments || ['Online', 'Offline', 'Packing', 'None']
            };
            refreshSearchableDropdowns();
        }
    } catch (err) {
        console.error('Failed to load master options:', err);
    }
}

function refreshSearchableDropdowns() {
    renderSearchableDropdownOptions('comp-type', masterOptionsCache.complaint_types);
    renderSearchableDropdownOptions('edit-complain-type', masterOptionsCache.complaint_types);
    renderSearchableDropdownOptions('comp-salesperson-name', masterOptionsCache.salespersons);
    renderSearchableDropdownOptions('edit-salesperson-name', masterOptionsCache.salespersons);
}

function renderSearchableDropdownOptions(inputId, optionsList, currentValue = '') {
    const listEl = document.getElementById(`${inputId}-options`);
    if (!listEl) return;

    let combined = [...(optionsList || [])];
    if (currentValue && !combined.some(o => o.toLowerCase() === currentValue.toLowerCase())) {
        combined.unshift(currentValue);
    }

    if (!combined.length) {
        listEl.innerHTML = '<div class="searchable-select-empty">No options registered</div>';
        return;
    }

    const cur = (document.getElementById(inputId)?.value || currentValue || '').trim().toLowerCase();
    listEl.innerHTML = combined.map(opt => {
        const isSel = opt.toLowerCase() === cur;
        const isHistorical = currentValue && opt.toLowerCase() === currentValue.toLowerCase() && !(optionsList || []).some(o => o.toLowerCase() === opt.toLowerCase());
        return `
            <div class="searchable-select-item ${isSel ? 'is-selected' : ''}" 
                 data-value="${escapeHtml(opt)}" 
                 onclick="selectSearchableOption('${inputId}', '${escapeHtml(opt).replace(/'/g, "\\'")}')">
                <span>${escapeHtml(opt)}</span>
                ${isHistorical ? '<small class="text-muted" style="font-size:0.75rem;">(Historical)</small>' : (isSel ? '<span>✓</span>' : '')}
            </div>
        `;
    }).join('');
}

function toggleSearchableSelect(inputId) {
    const dropdown = document.getElementById(`${inputId}-dropdown`);
    if (!dropdown) return;
    const isVisible = dropdown.style.display === 'block';
    closeAllSearchableSelects();
    if (!isVisible) {
        openSearchableSelect(inputId);
    }
}

function openSearchableSelect(inputId) {
    closeAllSearchableSelects();
    const dropdown = document.getElementById(`${inputId}-dropdown`);
    const input = document.getElementById(inputId);
    if (!dropdown || !input) return;

    activeSearchableInputId = inputId;
    dropdown.style.display = 'block';

    filterSearchableOptions(inputId, input.value.trim());

    if (!input._hasSearchableListener) {
        input._hasSearchableListener = true;
        input.addEventListener('input', () => {
            if (dropdown.style.display !== 'block') {
                dropdown.style.display = 'block';
                activeSearchableInputId = inputId;
            }
            filterSearchableOptions(inputId, input.value.trim());
        });
        input.addEventListener('focus', () => {
            openSearchableSelect(inputId);
        });
        input.addEventListener('keydown', (e) => {
            handleSearchableKeydown(e, inputId);
        });
    }
}

function closeAllSearchableSelects() {
    document.querySelectorAll('.searchable-select-dropdown').forEach(dd => {
        dd.style.display = 'none';
    });
    activeSearchableInputId = null;
}

function filterSearchableOptions(inputId, query) {
    const listEl = document.getElementById(`${inputId}-options`);
    if (!listEl) return;

    let sourceList = [];
    if (inputId.includes('type')) {
        sourceList = masterOptionsCache.complaint_types || [];
    } else if (inputId.includes('salesperson')) {
        sourceList = masterOptionsCache.salespersons || [];
    }

    const currentInputVal = document.getElementById(inputId)?.value?.trim() || '';
    let combined = [...sourceList];
    if (currentInputVal && !combined.some(o => o.toLowerCase() === currentInputVal.toLowerCase())) {
        combined.unshift(currentInputVal);
    }

    const cleanQ = (query || '').toLowerCase();
    const filtered = cleanQ ? combined.filter(opt => opt.toLowerCase().includes(cleanQ)) : combined;

    if (!filtered.length) {
        listEl.innerHTML = '<div class="searchable-select-empty">No matching options</div>';
        return;
    }

    listEl.innerHTML = filtered.map(opt => {
        const isSel = opt.toLowerCase() === currentInputVal.toLowerCase();
        const isHistorical = currentInputVal && opt.toLowerCase() === currentInputVal.toLowerCase() && !sourceList.some(o => o.toLowerCase() === opt.toLowerCase());
        return `
            <div class="searchable-select-item ${isSel ? 'is-selected' : ''}" 
                 data-value="${escapeHtml(opt)}" 
                 onclick="selectSearchableOption('${inputId}', '${escapeHtml(opt).replace(/'/g, "\\'")}')">
                <span>${escapeHtml(opt)}</span>
                ${isHistorical ? '<small class="text-muted" style="font-size:0.75rem;">(Historical)</small>' : (isSel ? '<span>✓</span>' : '')}
            </div>
        `;
    }).join('');
}

function selectSearchableOption(inputId, value) {
    const input = document.getElementById(inputId);
    if (input) {
        input.value = value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }
    closeAllSearchableSelects();
}

function handleSearchableKeydown(e, inputId) {
    const dropdown = document.getElementById(`${inputId}-dropdown`);
    if (!dropdown || dropdown.style.display !== 'block') {
        if (e.key === 'ArrowDown' || e.key === 'Down') {
            e.preventDefault();
            openSearchableSelect(inputId);
        }
        return;
    }

    const items = Array.from(dropdown.querySelectorAll('.searchable-select-item'));
    if (!items.length) return;

    let focusedIdx = items.findIndex(el => el.classList.contains('is-focused'));

    if (e.key === 'ArrowDown' || e.key === 'Down') {
        e.preventDefault();
        if (focusedIdx >= 0) items[focusedIdx].classList.remove('is-focused');
        focusedIdx = (focusedIdx + 1) % items.length;
        items[focusedIdx].classList.add('is-focused');
        items[focusedIdx].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'ArrowUp' || e.key === 'Up') {
        e.preventDefault();
        if (focusedIdx >= 0) items[focusedIdx].classList.remove('is-focused');
        focusedIdx = (focusedIdx - 1 + items.length) % items.length;
        items[focusedIdx].classList.add('is-focused');
        items[focusedIdx].scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter') {
        if (focusedIdx >= 0 && items[focusedIdx]) {
            e.preventDefault();
            const val = items[focusedIdx].getAttribute('data-value');
            if (val) selectSearchableOption(inputId, val);
        }
    } else if (e.key === 'Escape' || e.key === 'Esc') {
        e.preventDefault();
        closeAllSearchableSelects();
    }
}

// =============================================================================
// MASTER DATA MANAGEMENT CRUD CONTROLLERS (Complaint Types & Salespersons)
// =============================================================================

async function loadMasterData() {
    await Promise.all([
        loadMasterComplaintTypes(),
        loadMasterSalespersons()
    ]);
}

async function loadMasterComplaintTypes() {
    const tbody = document.getElementById('master-ct-table-body');
    if (!tbody) return;
    try {
        const res = await fetch('/api/master/complaint-types?all=true');
        const data = await res.json();
        if (res.ok && data.complaint_types) {
            masterComplaintTypesList = data.complaint_types;
            filterMasterComplaintTypes();
        } else {
            tbody.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-danger">${data.message || 'Failed to load complaint types'}</td></tr>`;
        }
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-danger">Network error loading complaint types: ${e.message}</td></tr>`;
    }
}

function filterMasterComplaintTypes() {
    const tbody = document.getElementById('master-ct-table-body');
    if (!tbody) return;

    const q = (document.getElementById('master-ct-search')?.value || '').trim().toLowerCase();
    const statusFilter = document.getElementById('master-ct-status-filter')?.value || 'ALL';

    let filtered = masterComplaintTypesList.filter(item => {
        const matchesQ = !q || (item.Name || '').toLowerCase().includes(q) || 
                               (item.Description || '').toLowerCase().includes(q) || 
                               (item['Type ID'] || '').toLowerCase().includes(q);
        const isActive = String(item['Is Active']).toUpperCase() === 'TRUE';
        const matchesStatus = (statusFilter === 'ALL') || 
                              (statusFilter === 'ACTIVE' && isActive) || 
                              (statusFilter === 'INACTIVE' && !isActive);
        return matchesQ && matchesStatus;
    });

    if (!filtered.length) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-muted">No complaint types found matching criteria.</td></tr>`;
        return;
    }

    tbody.innerHTML = filtered.map(t => {
        const isActive = String(t['Is Active']).toUpperCase() === 'TRUE';
        const safeId = escapeHtml(t['Type ID'] || '');
        const safeName = escapeHtml(t.Name || '');
        const safeDesc = escapeHtml(t.Description || '');
        return `
            <tr>
                <td><code>${safeId}</code></td>
                <td><strong>${safeName}</strong></td>
                <td><span class="text-muted" style="font-size: 0.85rem;">${safeDesc || '—'}</span></td>
                <td>
                    <span class="badge ${isActive ? 'badge-status-resolved' : 'badge-status-closed'}">
                        ${isActive ? 'Active' : 'Inactive'}
                    </span>
                </td>
                <td>
                    <div style="display: flex; gap: 6px;">
                        <button class="btn btn-secondary btn-sm" onclick="openEditComplaintTypeModal('${safeId}')">Edit</button>
                        <button class="btn ${isActive ? 'btn-outline-danger' : 'btn-success'} btn-sm" onclick="toggleComplaintTypeStatus('${safeId}', ${isActive})">
                            ${isActive ? 'Deactivate' : 'Activate'}
                        </button>
                        <button class="btn btn-outline-danger btn-sm" onclick="promptDeleteMasterItem('complaint-type', '${safeId}', '${safeName.replace(/'/g, "\\'")}')">Delete</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function openCreateComplaintTypeModal() {
    document.getElementById('modal-ct-title').textContent = 'Add Complaint Type';
    document.getElementById('modal-ct-id').value = '';
    document.getElementById('modal-ct-id-group').style.display = 'none';
    document.getElementById('modal-ct-name').value = '';
    document.getElementById('modal-ct-desc').value = '';
    document.getElementById('modal-ct-status-group').style.display = 'none';
    document.getElementById('modal-ct-status').value = 'TRUE';
    document.getElementById('modal-ct-submit-btn').textContent = 'Create Complaint Type';
    document.getElementById('modal-master-complaint-type').style.display = 'flex';
    document.getElementById('modal-ct-name').focus();
}

function openEditComplaintTypeModal(typeId) {
    const item = masterComplaintTypesList.find(t => t['Type ID'] === typeId);
    if (!item) {
        showToast('Complaint Type not found.', 'error');
        return;
    }
    document.getElementById('modal-ct-title').textContent = `Edit Complaint Type: ${item['Type ID']}`;
    document.getElementById('modal-ct-id').value = item['Type ID'];
    document.getElementById('modal-ct-id-display').value = item['Type ID'];
    document.getElementById('modal-ct-id-group').style.display = 'block';
    document.getElementById('modal-ct-name').value = item.Name || '';
    document.getElementById('modal-ct-desc').value = item.Description || '';
    document.getElementById('modal-ct-status-group').style.display = 'block';
    document.getElementById('modal-ct-status').value = String(item['Is Active']).toUpperCase() === 'TRUE' ? 'TRUE' : 'FALSE';
    document.getElementById('modal-ct-submit-btn').textContent = 'Save Changes';
    document.getElementById('modal-master-complaint-type').style.display = 'flex';
}

function closeMasterComplaintTypeModal() {
    document.getElementById('modal-master-complaint-type').style.display = 'none';
}

async function submitMasterComplaintType(e) {
    e.preventDefault();
    const typeId = document.getElementById('modal-ct-id').value.trim();
    const name = document.getElementById('modal-ct-name').value.trim();
    const description = document.getElementById('modal-ct-desc').value.trim();
    const is_active = document.getElementById('modal-ct-status').value;
    const btn = document.getElementById('modal-ct-submit-btn');

    if (!name) {
        showToast('Complaint type name is required.', 'error');
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Saving...';

    try {
        const url = typeId ? `/api/master/complaint-types/${typeId}` : '/api/master/complaint-types';
        const method = typeId ? 'PUT' : 'POST';
        const res = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, description, is_active })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
            closeMasterComplaintTypeModal();
            await loadMasterData();
            await loadMasterOptions();
        } else {
            showToast(data.message || 'Operation failed.', 'error');
        }
    } catch (err) {
        showToast(`Network error: ${err.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = typeId ? 'Save Changes' : 'Create Complaint Type';
    }
}

async function toggleComplaintTypeStatus(typeId, currentIsActive) {
    const item = masterComplaintTypesList.find(t => t['Type ID'] === typeId);
    if (!item) return;
    const newStatus = currentIsActive ? 'FALSE' : 'TRUE';
    try {
        const res = await fetch(`/api/master/complaint-types/${typeId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: item.Name,
                description: item.Description || '',
                is_active: newStatus
            })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(`Complaint Type '${item.Name}' is now ${newStatus === 'TRUE' ? 'Active' : 'Inactive'}.`, 'success');
            await loadMasterData();
            await loadMasterOptions();
        } else {
            showToast(data.message || 'Failed to update status.', 'error');
        }
    } catch (e) {
        showToast(`Network error: ${e.message}`, 'error');
    }
}

// --- Salespersons Management ---

async function loadMasterSalespersons() {
    const tbody = document.getElementById('master-sp-table-body');
    if (!tbody) return;
    try {
        const res = await fetch('/api/master/salespersons?all=true');
        const data = await res.json();
        if (res.ok && data.salespersons) {
            masterSalespersonsList = data.salespersons;
            filterMasterSalespersons();
        } else {
            tbody.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-danger">${data.message || 'Failed to load salespersons'}</td></tr>`;
        }
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-danger">Network error loading salespersons: ${e.message}</td></tr>`;
    }
}

function filterMasterSalespersons() {
    const tbody = document.getElementById('master-sp-table-body');
    if (!tbody) return;

    const q = (document.getElementById('master-sp-search')?.value || '').trim().toLowerCase();
    const statusFilter = document.getElementById('master-sp-status-filter')?.value || 'ALL';

    let filtered = masterSalespersonsList.filter(item => {
        const matchesQ = !q || (item.Name || '').toLowerCase().includes(q) || 
                               (item.Phone || '').toLowerCase().includes(q) || 
                               (item.Email || '').toLowerCase().includes(q) || 
                               (item['Salesperson ID'] || '').toLowerCase().includes(q);
        const isActive = String(item['Is Active']).toUpperCase() === 'TRUE';
        const matchesStatus = (statusFilter === 'ALL') || 
                              (statusFilter === 'ACTIVE' && isActive) || 
                              (statusFilter === 'INACTIVE' && !isActive);
        return matchesQ && matchesStatus;
    });

    if (!filtered.length) {
        tbody.innerHTML = `<tr><td colspan="5" class="text-center py-4 text-muted">No salespersons found matching criteria.</td></tr>`;
        return;
    }

    tbody.innerHTML = filtered.map(s => {
        const isActive = String(s['Is Active']).toUpperCase() === 'TRUE';
        const safeId = escapeHtml(s['Salesperson ID'] || '');
        const safeName = escapeHtml(s.Name || '');
        const safePhone = escapeHtml(s.Phone || '');
        const safeEmail = escapeHtml(s.Email || '');
        const contactInfo = [safePhone, safeEmail].filter(Boolean).join(' &bull; ');
        return `
            <tr>
                <td><code>${safeId}</code></td>
                <td><strong>${safeName}</strong></td>
                <td><span class="text-muted" style="font-size: 0.85rem;">${contactInfo || '—'}</span></td>
                <td>
                    <span class="badge ${isActive ? 'badge-status-resolved' : 'badge-status-closed'}">
                        ${isActive ? 'Active' : 'Inactive'}
                    </span>
                </td>
                <td>
                    <div style="display: flex; gap: 6px;">
                        <button class="btn btn-secondary btn-sm" onclick="openEditSalespersonModal('${safeId}')">Edit</button>
                        <button class="btn ${isActive ? 'btn-outline-danger' : 'btn-success'} btn-sm" onclick="toggleSalespersonStatus('${safeId}', ${isActive})">
                            ${isActive ? 'Deactivate' : 'Activate'}
                        </button>
                        <button class="btn btn-outline-danger btn-sm" onclick="promptDeleteMasterItem('salesperson', '${safeId}', '${safeName.replace(/'/g, "\\'")}')">Delete</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function openCreateSalespersonModal() {
    document.getElementById('modal-sp-title').textContent = 'Add Salesperson';
    document.getElementById('modal-sp-id').value = '';
    document.getElementById('modal-sp-id-group').style.display = 'none';
    document.getElementById('modal-sp-name').value = '';
    document.getElementById('modal-sp-phone').value = '';
    document.getElementById('modal-sp-email').value = '';
    document.getElementById('modal-sp-status-group').style.display = 'none';
    document.getElementById('modal-sp-status').value = 'TRUE';
    document.getElementById('modal-sp-submit-btn').textContent = 'Create Salesperson';
    document.getElementById('modal-master-salesperson').style.display = 'flex';
    document.getElementById('modal-sp-name').focus();
}

function openEditSalespersonModal(spId) {
    const item = masterSalespersonsList.find(s => s['Salesperson ID'] === spId);
    if (!item) {
        showToast('Salesperson not found.', 'error');
        return;
    }
    document.getElementById('modal-sp-title').textContent = `Edit Salesperson: ${item['Salesperson ID']}`;
    document.getElementById('modal-sp-id').value = item['Salesperson ID'];
    document.getElementById('modal-sp-id-display').value = item['Salesperson ID'];
    document.getElementById('modal-sp-id-group').style.display = 'block';
    document.getElementById('modal-sp-name').value = item.Name || '';
    document.getElementById('modal-sp-phone').value = item.Phone || '';
    document.getElementById('modal-sp-email').value = item.Email || '';
    document.getElementById('modal-sp-status-group').style.display = 'block';
    document.getElementById('modal-sp-status').value = String(item['Is Active']).toUpperCase() === 'TRUE' ? 'TRUE' : 'FALSE';
    document.getElementById('modal-sp-submit-btn').textContent = 'Save Changes';
    document.getElementById('modal-master-salesperson').style.display = 'flex';
}

function closeMasterSalespersonModal() {
    document.getElementById('modal-master-salesperson').style.display = 'none';
}

async function submitMasterSalesperson(e) {
    e.preventDefault();
    const spId = document.getElementById('modal-sp-id').value.trim();
    const name = document.getElementById('modal-sp-name').value.trim();
    const phone = document.getElementById('modal-sp-phone').value.trim();
    const email = document.getElementById('modal-sp-email').value.trim();
    const is_active = document.getElementById('modal-sp-status').value;
    const btn = document.getElementById('modal-sp-submit-btn');

    if (!name) {
        showToast('Salesperson name is required.', 'error');
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Saving...';

    try {
        const url = spId ? `/api/master/salespersons/${spId}` : '/api/master/salespersons';
        const method = spId ? 'PUT' : 'POST';
        const res = await fetch(url, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, phone, email, is_active })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
            closeMasterSalespersonModal();
            await loadMasterData();
            await loadMasterOptions();
        } else {
            showToast(data.message || 'Operation failed.', 'error');
        }
    } catch (err) {
        showToast(`Network error: ${err.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = spId ? 'Save Changes' : 'Create Salesperson';
    }
}

async function toggleSalespersonStatus(spId, currentIsActive) {
    const item = masterSalespersonsList.find(s => s['Salesperson ID'] === spId);
    if (!item) return;
    const newStatus = currentIsActive ? 'FALSE' : 'TRUE';
    try {
        const res = await fetch(`/api/master/salespersons/${spId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: item.Name,
                phone: item.Phone || '',
                email: item.Email || '',
                is_active: newStatus
            })
        });
        const data = await res.json();
        if (res.ok) {
            showToast(`Salesperson '${item.Name}' is now ${newStatus === 'TRUE' ? 'Active' : 'Inactive'}.`, 'success');
            await loadMasterData();
            await loadMasterOptions();
        } else {
            showToast(data.message || 'Failed to update status.', 'error');
        }
    } catch (e) {
        showToast(`Network error: ${e.message}`, 'error');
    }
}

// --- Delete & Usage Verification ---

let activeMasterDeleteType = null;
let activeMasterDeleteId = null;

async function promptDeleteMasterItem(type, id, name) {
    activeMasterDeleteType = type;
    activeMasterDeleteId = id;

    const modal = document.getElementById('modal-master-delete');
    document.getElementById('modal-master-delete-type').value = type;
    document.getElementById('modal-master-delete-id').value = id;
    document.getElementById('modal-master-delete-item-name').textContent = name;
    document.getElementById('modal-master-delete-item-id').textContent = id;
    document.getElementById('modal-master-delete-title').textContent = type === 'complaint-type' ? 'Delete Complaint Type' : 'Delete Salesperson';

    const loadingEl = document.getElementById('modal-master-delete-loading');
    const blockedEl = document.getElementById('modal-master-delete-blocked');
    const allowedEl = document.getElementById('modal-master-delete-allowed');
    const deactivateBtn = document.getElementById('modal-master-deactivate-instead-btn');
    const deleteBtn = document.getElementById('modal-master-confirm-delete-btn');

    loadingEl.style.display = 'block';
    blockedEl.style.display = 'none';
    allowedEl.style.display = 'none';
    deactivateBtn.style.display = 'none';
    deleteBtn.style.display = 'none';

    modal.style.display = 'flex';

    try {
        const usageEndpoint = type === 'complaint-type' ? 
            `/api/master/complaint-types/${id}/usage` : 
            `/api/master/salespersons/${id}/usage`;
        const res = await fetch(usageEndpoint);
        const data = await res.json();

        loadingEl.style.display = 'none';
        if (res.ok && data.status === 'success') {
            const count = data.usage_count || 0;
            if (count > 0) {
                document.getElementById('modal-master-delete-usage-count').textContent = count;
                blockedEl.style.display = 'block';
                deactivateBtn.style.display = 'inline-block';
                deleteBtn.style.display = 'none';
            } else {
                allowedEl.style.display = 'block';
                deleteBtn.style.display = 'inline-block';
            }
        } else {
            showToast(data.message || 'Failed to check usage.', 'error');
            closeMasterDeleteModal();
        }
    } catch (e) {
        loadingEl.textContent = `Error checking usage: ${e.message}`;
    }
}

function closeMasterDeleteModal() {
    document.getElementById('modal-master-delete').style.display = 'none';
    activeMasterDeleteType = null;
    activeMasterDeleteId = null;
}

async function executeDeactivateInstead() {
    if (!activeMasterDeleteType || !activeMasterDeleteId) return;
    const type = activeMasterDeleteType;
    const id = activeMasterDeleteId;
    closeMasterDeleteModal();
    if (type === 'complaint-type') {
        await toggleComplaintTypeStatus(id, true);
    } else {
        await toggleSalespersonStatus(id, true);
    }
}

async function executeMasterDelete() {
    if (!activeMasterDeleteType || !activeMasterDeleteId) return;
    const type = activeMasterDeleteType;
    const id = activeMasterDeleteId;
    const btn = document.getElementById('modal-master-confirm-delete-btn');
    btn.disabled = true;
    btn.textContent = 'Deleting...';

    try {
        const endpoint = type === 'complaint-type' ? 
            `/api/master/complaint-types/${id}` : 
            `/api/master/salespersons/${id}`;
        const res = await fetch(endpoint, { method: 'DELETE' });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
            closeMasterDeleteModal();
            await loadMasterData();
            await loadMasterOptions();
        } else {
            showToast(data.message || 'Deletion blocked.', 'error');
        }
    } catch (e) {
        showToast(`Network error: ${e.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Delete Permanently';
    }
}


// =============================================================================
// ENTERPRISE RBAC & AUDIT TRAIL LOGIC
// =============================================================================

async function loadRolesDropdownForUsers() {
    try {
        if (!allRolesCache || allRolesCache.length === 0) {
            const res = await fetch('/api/rbac/roles');
            if (res.ok) {
                const data = await res.json();
                allRolesCache = data.roles || [];
            }
        }
        if (!allRolesCache || allRolesCache.length === 0) return;

        const isSuperAdmin = currentUser && currentUser.role === 'Super Admin';
        const roleDropdowns = ['modal-user-role', 'edit-user-role'];

        roleDropdowns.forEach(dropdownId => {
            const el = document.getElementById(dropdownId);
            if (!el) return;
            const currentVal = el.value;

            el.innerHTML = allRolesCache.map(r => {
                const isSuper = r.role_name === 'Super Admin';
                const disabled = isSuper && !isSuperAdmin ? 'disabled' : '';
                const systemBadge = r.is_system ? ' (System)' : '';
                return `<option value="${escapeHtml(r.role_name)}" ${disabled} ${isSuper ? 'id="opt-edit-role-super-admin"' : ''}>${escapeHtml(r.role_name)}${systemBadge}</option>`;
            }).join('');

            if (currentVal && Array.from(el.options).some(o => o.value === currentVal)) {
                el.value = currentVal;
            }
        });
    } catch (e) {
        console.warn('Could not populate dynamic user roles dropdown:', e);
    }
}

// -----------------------------------------------------------------------------
// ROLES MANAGEMENT VIEW
// -----------------------------------------------------------------------------

async function loadRolesData() {
    const tbody = document.getElementById('roles-table-body');
    if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted" style="padding: 24px;">Loading role configurations from Google Sheets...</td></tr>';

    try {
        const [rolesRes, permsRes] = await Promise.all([
            fetch('/api/rbac/roles'),
            fetch('/api/rbac/permissions')
        ]);

        if (rolesRes.ok && permsRes.ok) {
            const rolesData = await rolesRes.json();
            const permsData = await permsRes.json();
            allRolesCache = rolesData.roles || [];
            allPermissionsCache = permsData.permissions || [];
            renderRolesTable();
            populateRoleCloneDropdown();
        } else {
            if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="text-center text-danger" style="padding: 24px;">Failed to load roles from server.</td></tr>';
        }
    } catch (err) {
        console.error('Failed to load roles:', err);
        if (tbody) tbody.innerHTML = `<tr><td colspan="6" class="text-center text-danger" style="padding: 24px;">Error: ${escapeHtml(err.message)}</td></tr>`;
    }
}

function renderRolesTable() {
    const tbody = document.getElementById('roles-table-body');
    if (!tbody) return;

    if (!allRolesCache || allRolesCache.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted" style="padding: 24px;">No roles configured.</td></tr>';
        return;
    }

    const isSuperAdmin = currentUser && currentUser.role === 'Super Admin';

    tbody.innerHTML = allRolesCache.map(r => {
        const isSystem = !!r.is_system;
        const scope = r.data_scope || 'OWN';
        let scopeColor = '#64748b';
        let scopeBg = '#f1f5f9';
        if (scope === 'ALL') { scopeColor = '#3730a3'; scopeBg = '#e0e7ff'; }
        else if (scope === 'DEPARTMENT') { scopeColor = '#065f46'; scopeBg = '#ecfdf5'; }
        else if (scope === 'OWN') { scopeColor = '#92400e'; scopeBg = '#fef3c7'; }

        const permsList = r.permissions || [];
        const isWildcard = permsList.includes('*');
        const permBadge = isWildcard ? 
            '<span class="badge" style="background: #eff6ff; color: #1d4ed8; font-weight: 600;">Full Access (*)</span>' : 
            `<span class="badge" style="background: #f8fafc; color: #334155;">${permsList.length} of ${allPermissionsCache.length || 28} perms</span>`;

        let actionBtns = '';
        if (isSuperAdmin) {
            actionBtns += `<button type="button" class="btn btn-outline-primary btn-sm" style="margin-right: 4px;" onclick="openEditRoleModal('${escapeHtml(r.role_id)}')" title="Edit Role and Permissions">Edit</button>`;
            actionBtns += `<button type="button" class="btn btn-outline-secondary btn-sm" style="margin-right: 4px;" onclick="openCloneRoleModal('${escapeHtml(r.role_id)}')" title="Clone as new custom role">Clone</button>`;
            if (!isSystem) {
                actionBtns += `<button type="button" class="btn btn-outline-danger btn-sm" onclick="openDeleteRoleModal('${escapeHtml(r.role_id)}', '${escapeHtml(r.role_name)}')" title="Delete custom role">Delete</button>`;
            }
        } else {
            actionBtns = '<span class="text-muted" style="font-size: 0.8rem;">Read-only</span>';
        }

        return `
            <tr>
                <td>
                    <div style="font-weight: 600; color: #0f172a;">${escapeHtml(r.role_name)}</div>
                    <small class="text-muted">${escapeHtml(r.description || '')}</small>
                </td>
                <td>
                    <span class="badge" style="background: ${isSystem ? '#e0f2fe; color: #0369a1;' : '#f3e8ff; color: #7e22ce;'}">
                        ${isSystem ? 'System' : 'Custom'}
                    </span>
                </td>
                <td>
                    <span class="badge" style="background: ${scopeBg}; color: ${scopeColor}; font-weight: 600;">
                        ${escapeHtml(scope)}
                    </span>
                </td>
                <td>
                    <span style="font-family: monospace; font-size: 0.9rem;">${escapeHtml(String(r.assigned_users_count || 0))}</span> users
                </td>
                <td>${permBadge}</td>
                <td>${actionBtns}</td>
            </tr>
        `;
    }).join('');
}

function populateRoleCloneDropdown() {
    const sel = document.getElementById('role-builder-clone-source');
    if (!sel) return;
    const currentVal = sel.value;

    sel.innerHTML = '<option value="">-- Choose a template to pre-fill --</option>' +
        allRolesCache.map(r => `<option value="${escapeHtml(r.role_name)}">${escapeHtml(r.role_name)} (${escapeHtml(r.data_scope || 'OWN')})</option>`).join('');

    if (currentVal) sel.value = currentVal;
}

function renderPermissionsMatrix(selectedPerms = [], isSuperAdminRole = false) {
    const container = document.getElementById('role-builder-permissions-matrix');
    if (!container) return;

    if (!allPermissionsCache || allPermissionsCache.length === 0) {
        container.innerHTML = '<div class="text-muted p-2">Zero permissions found.</div>';
        return;
    }

    // Group by category
    const grouped = {};
    allPermissionsCache.forEach(p => {
        const cat = p.category || 'General';
        if (!grouped[cat]) grouped[cat] = [];
        grouped[cat].push(p);
    });

    const isWildcard = selectedPerms.includes('*');

    container.innerHTML = Object.keys(grouped).map(cat => {
        const perms = grouped[cat];
        const allInCatChecked = !isWildcard && perms.every(p => selectedPerms.includes(p.id));

        const permCheckboxes = perms.map(p => {
            const checked = isWildcard || selectedPerms.includes(p.id) ? 'checked' : '';
            const disabled = isSuperAdminRole ? 'disabled' : '';
            return `
                <label style="display: flex; align-items: flex-start; gap: 8px; font-size: 0.82rem; background: #fff; padding: 6px 10px; border-radius: 4px; border: 1px solid var(--border); cursor: pointer;">
                    <input type="checkbox" class="role-perm-checkbox" data-category="${escapeHtml(cat)}" value="${escapeHtml(p.id)}" ${checked} ${disabled} onchange="onPermCheckboxChanged('${escapeHtml(cat)}')">
                    <div>
                        <div style="font-weight: 500; color: #1e293b;">${escapeHtml(p.name)}</div>
                        <div class="text-muted" style="font-size: 0.75rem;"><code>${escapeHtml(p.id)}</code> &bull; ${escapeHtml(p.description)}</div>
                    </div>
                </label>
            `;
        }).join('');

        return `
            <div class="perm-module-group" style="margin-bottom: 14px; background: #f8fafc; border: 1px solid var(--border); border-radius: 6px; padding: 10px 12px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <strong style="font-size: 0.88rem; color: #1e3a8a;">${escapeHtml(cat)}</strong>
                    <label style="font-size: 0.78rem; color: #64748b; cursor: pointer; display: flex; align-items: center; gap: 4px;">
                        <input type="checkbox" id="cat-toggle-${escapeHtml(cat.replace(/\s+/g, '-'))}" ${allInCatChecked || isWildcard ? 'checked' : ''} ${isSuperAdminRole ? 'disabled' : ''} onchange="toggleCategoryPerms('${escapeHtml(cat)}', this.checked)">
                        Select All ${escapeHtml(cat)}
                    </label>
                </div>
                <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 8px;">
                    ${permCheckboxes}
                </div>
            </div>
        `;
    }).join('');
}

function onPermCheckboxChanged(category) {
    const catCheckboxes = document.querySelectorAll(`.role-perm-checkbox[data-category="${category}"]`);
    const allChecked = Array.from(catCheckboxes).every(cb => cb.checked);
    const catToggle = document.getElementById(`cat-toggle-${category.replace(/\s+/g, '-')}`);
    if (catToggle) catToggle.checked = allChecked;
}

function toggleCategoryPerms(category, isChecked) {
    const catCheckboxes = document.querySelectorAll(`.role-perm-checkbox[data-category="${category}"]`);
    catCheckboxes.forEach(cb => {
        if (!cb.disabled) cb.checked = isChecked;
    });
}

function setAllPermissions(enable) {
    document.querySelectorAll('.role-perm-checkbox').forEach(cb => {
        if (!cb.disabled) cb.checked = enable;
    });
    document.querySelectorAll('[id^="cat-toggle-"]').forEach(cb => {
        if (!cb.disabled) cb.checked = enable;
    });
}

function applyRoleTemplateClone() {
    const sel = document.getElementById('role-builder-clone-source');
    if (!sel || !sel.value) return;
    const tplRole = allRolesCache.find(r => r.role_name === sel.value);
    if (!tplRole) return;

    document.getElementById('role-builder-scope').value = tplRole.data_scope || 'OWN';
    renderPermissionsMatrix(tplRole.permissions || [], false);
}

function openCreateRoleModal() {
    document.getElementById('role-builder-mode').value = 'create';
    document.getElementById('role-builder-original-id').value = '';
    document.getElementById('modal-role-builder-title').textContent = 'Create Custom Role';
    document.getElementById('role-builder-name').value = '';
    document.getElementById('role-builder-name').disabled = false;
    document.getElementById('role-builder-name-hint').style.display = 'none';
    document.getElementById('role-builder-scope').value = 'DEPARTMENT';
    document.getElementById('role-builder-desc').value = '';
    document.getElementById('role-builder-clone-source').value = '';

    renderPermissionsMatrix([], false);
    document.getElementById('modal-role-builder').style.display = 'flex';
    syncBodyModalOpen();
}

function openEditRoleModal(roleId) {
    const role = allRolesCache.find(r => r.role_id === roleId);
    if (!role) {
        showToast('Role configuration not found.', 'error');
        return;
    }

    document.getElementById('role-builder-mode').value = 'edit';
    document.getElementById('role-builder-original-id').value = role.role_id;
    document.getElementById('modal-role-builder-title').textContent = `Edit Role: ${role.role_name}`;
    document.getElementById('role-builder-name').value = role.role_name;

    const isSystem = !!role.is_system;
    const isSuperAdminRole = role.role_name === 'Super Admin';

    // System roles cannot be renamed
    document.getElementById('role-builder-name').disabled = isSystem;
    const hint = document.getElementById('role-builder-name-hint');
    if (hint) {
        hint.style.display = isSystem ? 'block' : 'none';
        hint.textContent = isSystem ? 'System default role names are protected and cannot be renamed.' : '';
    }

    document.getElementById('role-builder-scope').value = role.data_scope || 'OWN';
    document.getElementById('role-builder-desc').value = role.description || '';
    document.getElementById('role-builder-clone-source').value = '';

    renderPermissionsMatrix(role.permissions || [], isSuperAdminRole);
    document.getElementById('modal-role-builder').style.display = 'flex';
    syncBodyModalOpen();
}

function openCloneRoleModal(roleId) {
    const role = allRolesCache.find(r => r.role_id === roleId);
    if (!role) return;

    document.getElementById('role-builder-mode').value = 'create';
    document.getElementById('role-builder-original-id').value = '';
    document.getElementById('modal-role-builder-title').textContent = `Create Role (Cloned from ${role.role_name})`;
    document.getElementById('role-builder-name').value = `${role.role_name} Copy`;
    document.getElementById('role-builder-name').disabled = false;
    document.getElementById('role-builder-name-hint').style.display = 'none';
    document.getElementById('role-builder-scope').value = role.data_scope || 'OWN';
    document.getElementById('role-builder-desc').value = `Cloned from ${role.role_name}. ${role.description || ''}`.trim();
    document.getElementById('role-builder-clone-source').value = role.role_name;

    renderPermissionsMatrix(role.permissions || [], false);
    document.getElementById('modal-role-builder').style.display = 'flex';
    syncBodyModalOpen();
}

function closeRoleBuilderModal() {
    document.getElementById('modal-role-builder').style.display = 'none';
    syncBodyModalOpen();
}

async function submitRoleBuilder(e) {
    e.preventDefault();
    const mode = document.getElementById('role-builder-mode').value;
    const originalId = document.getElementById('role-builder-original-id').value;
    const roleName = document.getElementById('role-builder-name').value.trim();
    const dataScope = document.getElementById('role-builder-scope').value;
    const description = document.getElementById('role-builder-desc').value.trim();

    if (!roleName) {
        showToast('Role Name is required.', 'error');
        return;
    }

    // Collect permissions
    let permissions = [];
    if (roleName === 'Super Admin') {
        permissions = ['*'];
    } else {
        const checked = Array.from(document.querySelectorAll('.role-perm-checkbox:checked'));
        permissions = checked.map(cb => cb.value);
    }

    const submitBtn = document.getElementById('role-builder-submit-btn');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving Role...';

    try {
        const endpoint = mode === 'create' ? '/api/rbac/roles' : `/api/rbac/roles/${originalId}`;
        const method = mode === 'create' ? 'POST' : 'PUT';

        const payload = {
            role_name: roleName,
            data_scope: dataScope,
            description: description,
            permissions: permissions
        };

        const res = await fetch(endpoint, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
            closeRoleBuilderModal();
            await loadRolesData();
            await loadRolesDropdownForUsers();
        } else {
            showToast(data.message || 'Failed to save role.', 'error');
        }
    } catch (err) {
        showToast(`Network error: ${err.message}`, 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Save Role';
    }
}

let activeRoleDeleteId = null;

async function openDeleteRoleModal(roleId, roleName) {
    activeRoleDeleteId = roleId;
    document.getElementById('role-delete-id').value = roleId;
    document.getElementById('role-delete-name').textContent = roleName;

    const loadingEl = document.getElementById('role-delete-loading');
    const blockedEl = document.getElementById('role-delete-blocked');
    const allowedEl = document.getElementById('role-delete-allowed');
    const confirmBtn = document.getElementById('role-delete-confirm-btn');

    loadingEl.style.display = 'block';
    blockedEl.style.display = 'none';
    allowedEl.style.display = 'none';
    confirmBtn.style.display = 'none';

    document.getElementById('modal-role-delete').style.display = 'flex';
    syncBodyModalOpen();

    try {
        const res = await fetch(`/api/rbac/roles/${roleId}/usage`);
        const data = await res.json();
        loadingEl.style.display = 'none';

        if (res.ok) {
            const count = data.assigned_users_count || 0;
            if (count > 0) {
                document.getElementById('role-delete-usage-count').textContent = count;
                blockedEl.style.display = 'block';
                confirmBtn.style.display = 'none';
            } else {
                allowedEl.style.display = 'block';
                confirmBtn.style.display = 'inline-block';
            }
        } else {
            showToast(data.message || 'Error checking role usage.', 'error');
            closeRoleDeleteModal();
        }
    } catch (err) {
        loadingEl.style.display = 'none';
        showToast('Network error checking role usage.', 'error');
    }
}

function closeRoleDeleteModal() {
    activeRoleDeleteId = null;
    document.getElementById('modal-role-delete').style.display = 'none';
    syncBodyModalOpen();
}

async function executeRoleDelete() {
    if (!activeRoleDeleteId) return;
    const btn = document.getElementById('role-delete-confirm-btn');
    btn.disabled = true;
    btn.textContent = 'Deleting...';

    try {
        const res = await fetch(`/api/rbac/roles/${activeRoleDeleteId}`, { method: 'DELETE' });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, 'success');
            closeRoleDeleteModal();
            await loadRolesData();
            await loadRolesDropdownForUsers();
        } else {
            showToast(data.message || 'Failed to delete role.', 'error');
        }
    } catch (err) {
        showToast(`Network error: ${err.message}`, 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Delete Role Permanently';
    }
}

// -----------------------------------------------------------------------------
// SYSTEM SECURITY & AUDIT TRAIL VIEW
// -----------------------------------------------------------------------------

async function loadAuditTrailData() {
    const tbody = document.getElementById('audit-table-body');
    if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted" style="padding: 24px;">Querying audit logs from Google Sheets...</td></tr>';

    const module = document.getElementById('audit-filter-module')?.value || '';
    const action = document.getElementById('audit-filter-action')?.value || '';
    const search = document.getElementById('audit-filter-search')?.value.trim() || '';
    const limit = document.getElementById('audit-filter-limit')?.value || '100';

    const params = new URLSearchParams();
    if (module) params.set('module', module);
    if (action) params.set('action', action);
    if (search) params.set('search', search);
    if (limit) params.set('limit', limit);

    try {
        const res = await fetch(`/api/rbac/audit-logs?${params.toString()}`);
        if (res.ok) {
            const data = await res.json();
            auditTrailCache = data.logs || [];
            renderAuditTrailTable();
        } else {
            if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="text-center text-danger" style="padding: 24px;">Failed to load audit logs.</td></tr>';
        }
    } catch (err) {
        console.error('Failed to load audit trail:', err);
        if (tbody) tbody.innerHTML = `<tr><td colspan="8" class="text-center text-danger" style="padding: 24px;">Error: ${escapeHtml(err.message)}</td></tr>`;
    }
}

function debounceAuditSearch() {
    if (auditSearchDebounceTimer) clearTimeout(auditSearchDebounceTimer);
    auditSearchDebounceTimer = setTimeout(() => {
        loadAuditTrailData();
    }, 350);
}

function renderAuditTrailTable() {
    const tbody = document.getElementById('audit-table-body');
    if (!tbody) return;

    if (!auditTrailCache || auditTrailCache.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted" style="padding: 24px;">Zero audit events match the selected criteria.</td></tr>';
        return;
    }

    tbody.innerHTML = auditTrailCache.map(log => {
        const ts = log.Timestamp || '-';
        const user = log.User_Email || log.User_ID || 'System';
        const role = log.User_Role || '-';
        const action = log.Action || '-';
        const module = log.Module || '-';
        const recordId = log.Record_ID || '-';
        const dept = log.Department || '-';
        const result = String(log.Result || 'SUCCESS').toUpperCase();

        let actionBg = '#e2e8f0';
        let actionColor = '#334155';
        if (action.includes('CREATE') || action.includes('LOGIN')) { actionBg = '#dcfce7'; actionColor = '#15803d'; }
        else if (action.includes('UPDATE') || action.includes('STATUS')) { actionBg = '#e0f2fe'; actionColor = '#0369a1'; }
        else if (action.includes('DELETE') || action.includes('DENIED')) { actionBg = '#fee2e2'; actionColor = '#b91c1c'; }
        else if (action.includes('ASSIGN')) { actionBg = '#fef3c7'; actionColor = '#b45309'; }

        const resultBadge = result === 'SUCCESS' ? 
            '<span class="badge" style="background:#dcfce7;color:#15803d;font-weight:600;">SUCCESS</span>' : 
            '<span class="badge" style="background:#fee2e2;color:#b91c1c;font-weight:600;">FAILED</span>';

        let details = log.Details || '';
        let newState = log.New_State || '';
        let detailText = details;
        if (newState && !detailText.includes(newState)) {
            detailText += detailText ? ` | ${newState}` : newState;
        }

        return `
            <tr>
                <td style="font-size: 0.8rem; font-family: monospace; white-space: nowrap;">${escapeHtml(ts)}</td>
                <td>
                    <div style="font-weight: 500; font-size: 0.85rem;">${escapeHtml(user)}</div>
                    <small class="text-muted">${escapeHtml(role)}</small>
                </td>
                <td>
                    <span class="badge" style="background: ${actionBg}; color: ${actionColor}; font-weight: 600; font-size: 0.75rem;">
                        ${escapeHtml(action)}
                    </span>
                </td>
                <td style="font-size: 0.85rem;">${escapeHtml(module)}</td>
                <td style="font-family: monospace; font-size: 0.8rem;">${escapeHtml(recordId)}</td>
                <td style="font-size: 0.85rem;">${escapeHtml(dept)}</td>
                <td>${resultBadge}</td>
                <td style="font-size: 0.82rem; color: #475569; max-width: 320px; word-break: break-word;">${escapeHtml(detailText || '-')}</td>
            </tr>
        `;
    }).join('');
}

function exportAuditTrailCsv() {
    const module = document.getElementById('audit-filter-module')?.value || '';
    const action = document.getElementById('audit-filter-action')?.value || '';
    const search = document.getElementById('audit-filter-search')?.value.trim() || '';

    const params = new URLSearchParams();
    if (module) params.set('module', module);
    if (action) params.set('action', action);
    if (search) params.set('search', search);

    window.open(`/api/rbac/audit-logs/export?${params.toString()}`, '_blank');
}
