/**
 * Complaint Management System - Core SPA Client Logic
 */

let currentUser = null;
let currentDepartments = [];
let allStaffUsers = [];
let allComplaintsCache = [];
let activeComplaintId = null;
let driveDiagnostic = null;

document.addEventListener('DOMContentLoaded', () => {
    initApp();
});

async function initApp() {
    setupEventListeners();
    checkUrlAuthParams();
    await checkAuth();
    if (currentUser) {
        await loadDepartments();
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
    });
}

function handleHashRouting() {
    const hash = window.location.hash.replace('#', '') || 'dashboard';
    switchView(hash);
}

function switchView(viewName) {
    if (currentUser) {
        const isStaffOrAdmin = ['Super Admin', 'Admin', 'Support Staff'].includes(currentUser.role);
        const isAdmin = ['Super Admin', 'Admin'].includes(currentUser.role);
        if (viewName === 'manage' && !isStaffOrAdmin) {
            viewName = 'dashboard';
        }
        if (viewName === 'settings' && !isAdmin) {
            viewName = 'dashboard';
        }
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
            'settings': 'Users & System Settings'
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
        }

        // Trigger view-specific data refresh
        if (viewName === 'dashboard') loadDashboardData();
        if (viewName === 'my-complaints') loadMyComplaints();
        if (viewName === 'reports') initReportsView();
        if (viewName === 'manage') loadManageComplaints();
        if (viewName === 'settings') loadSettingsData();
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

    // RBAC Nav Visibility
    const isStaffOrAdmin = ['Super Admin', 'Admin', 'Support Staff'].includes(currentUser.role);
    const isAdmin = ['Super Admin', 'Admin'].includes(currentUser.role);

    document.querySelectorAll('.staff-only').forEach(el => el.style.display = isStaffOrAdmin ? 'block' : 'none');
    document.querySelectorAll('.admin-only').forEach(el => el.style.display = isAdmin ? 'block' : 'none');
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
    // Mode badge
    const modeBadge = document.getElementById('drive-auth-mode-badge');
    if (modeBadge) {
        if (drive.auth_mode === 'user_oauth') {
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
        if (drive.can_upload_binary && drive.drive_verified_success) {
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
        if (drive.can_upload_binary) {
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
        if (drive.storage_quota && drive.storage_quota.limit_bytes) {
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
        if (drive.can_upload_binary && drive.drive_verified_success) {
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
        if (drive.drive_verified_success) {
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

    // Toggle disconnect and authorize buttons
    const disconnectBtn = document.getElementById('btn-drive-disconnect');
    const authBtn = document.getElementById('btn-drive-authorize');
    if (disconnectBtn) {
        disconnectBtn.style.display = drive.auth_mode === 'user_oauth' ? 'inline-block' : 'none';
    }
    if (authBtn) {
        authBtn.textContent = drive.auth_mode === 'user_oauth' ? '🔄 Re-Authorize Google Drive' : '🔑 Authorize with Google';
    }
}

function updateUploadNotice(canUpload, drive = null) {
    const noticeEl = document.getElementById('lodge-upload-quota-notice');
    if (!noticeEl) return;
    if (canUpload) {
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
    const hiddenInput = document.getElementById(prefix === 'lodge' ? 'comp-dept' : 'edit-dept');
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
    const hiddenInput = document.getElementById(prefix === 'lodge' ? 'comp-dept' : 'edit-dept');
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
    const singleSelects = ['reg-dept', 'modal-user-dept', 'manage-dept-filter', 'delete-dept-reassign-select'];
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

    // 2. Multi-select options for lodge and edit
    ['lodge', 'edit'].forEach(prefix => {
        const container = document.getElementById(`${prefix}-dept-options`);
        if (!container) return;

        // Remember current selections if any
        const hiddenInput = document.getElementById(prefix === 'lodge' ? 'comp-dept' : 'edit-dept');
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

    const isAdmin = currentUser && ['Super Admin', 'Admin'].includes(currentUser.role);
    const isStaffOrAdmin = currentUser && ['Super Admin', 'Admin', 'Support Staff'].includes(currentUser.role);

    tbody.innerHTML = complaints.map(c => {
        const canEdit = isStaffOrAdmin || (c.Status === 'Submitted');
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
                        ${isAdmin ? `<button class="btn btn-outline-danger btn-sm" onclick="openDeleteModal('${c['Complaint ID']}')">Delete</button>` : ''}
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

    const isAdmin = currentUser && ['Super Admin', 'Admin'].includes(currentUser.role);

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
                    <button class="btn btn-warning btn-sm" onclick="openEditComplaintModal('${c['Complaint ID']}')">Edit</button>
                    ${isAdmin ? `<button class="btn btn-outline-danger btn-sm" onclick="openDeleteModal('${c['Complaint ID']}')">Delete</button>` : ''}
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
    const isStaffOrAdmin = currentUser && ['Super Admin', 'Admin', 'Support Staff'].includes(currentUser.role);
    const isAdmin = currentUser && ['Super Admin', 'Admin'].includes(currentUser.role);
    const isOwner = currentUser && (complaint['Complainant Email'] || '').toLowerCase() === (currentUser.email || '').toLowerCase();
    const isSubmitted = (complaint.Status || '').toLowerCase() === 'submitted';

    const editBtn = document.getElementById('modal-btn-edit');
    if (editBtn) {
        editBtn.style.display = (isStaffOrAdmin || (isOwner && isSubmitted)) ? 'inline-flex' : 'none';
    }

    const deleteBtn = document.getElementById('modal-btn-delete');
    if (deleteBtn) {
        deleteBtn.style.display = isAdmin ? 'inline-flex' : 'none';
    }

    // Staff Controls
    const staffBox = document.getElementById('modal-staff-controls');
    staffBox.style.display = isStaffOrAdmin ? 'block' : 'none';

    if (isStaffOrAdmin) {
        document.getElementById('modal-status-select').value = complaint.Status || 'Submitted';
        populateAssignSelect();
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
    document.getElementById('edit-complain-type').value = complaint['Complain Types'] || complaint.Subject || '';
    document.getElementById('edit-priority').value = complaint.Priority || 'Medium';
    document.getElementById('edit-category').value = complaint.Category || '';
    document.getElementById('edit-vendor-name').value = complaint['Vendor Name'] || '';

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
            allStaffUsers = (data.users || []).filter(u => ['Super Admin', 'Admin', 'Support Staff'].includes(u.Role));
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

async function loadAdminUsers() {
    try {
        const res = await fetch('/api/users');
        const data = await res.json();
        const tbody = document.getElementById('settings-users-table');
        if (res.ok && data.users) {
            tbody.innerHTML = data.users.map(u => `
                <tr>
                    <td><code>${u['User ID']}</code></td>
                    <td>${escapeHtml(u['Full Name'])}</td>
                    <td>${escapeHtml(u.Email)}</td>
                    <td><span class="badge ${u.Role.includes('Admin') ? 'badge-status-in-progress' : 'badge-prio-low'}">${u.Role}</span></td>
                    <td>${escapeHtml(u.Department || 'General')}</td>
                </tr>
            `).join('');
        }
    } catch (e) {
        console.error("Failed to load users:", e);
    }
}

function openCreateUserModal() {
    document.getElementById('create-user-modal').style.display = 'flex';
}
function closeCreateUserModal() {
    document.getElementById('create-user-modal').style.display = 'none';
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
    const isStaffOrAdmin = currentUser && ['Super Admin', 'Admin', 'Support Staff'].includes(currentUser.role);
    const canEdit = isStaffOrAdmin || (c.Status === 'Submitted');

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
