const state = {
  token: null,
  pollHandle: null,
};

async function api(path, options = {}) {
  const headers = options.headers || {};
  headers["Content-Type"] = "application/json";
  if (state.token) {
    headers["Authorization"] = `Bearer ${state.token}`;
  }
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${response.status}`);
  }
  return response.json();
}

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function fillConfig(config) {
  document.getElementById("deviceName").value = config.system.device_name || "";
  document.getElementById("webPort").value = config.web.port || 8080;
  document.getElementById("networkMode").value = config.network.mode || "dhcp";
  document.getElementById("staticIp").value = config.network.static_ip || "";
  document.getElementById("gateway").value = config.network.gateway || "";
  document.getElementById("dns").value = config.network.dns || "";
  document.getElementById("oxygenLow").value = config.alarms.oxygen_low || 19.5;
  document.getElementById("oxygenHigh").value = config.alarms.oxygen_high || 23.5;
  document.getElementById("coHigh").value = config.alarms.co_high || 50;
}

async function refreshConfig() {
  const config = await api("/api/config");
  fillConfig(config);
}

async function refreshMeasurements() {
  const data = await api("/api/measurements");
  const statusEl = document.getElementById("statusText");
  statusEl.textContent = data.status;
  statusEl.className = `value status-${String(data.status).toLowerCase()}`;
  setText("metaText", `${data.device_name} | IP ${data.ip_address} | Updated ${data.last_update || "--"}`);
  setText("oxygenValue", data.measurements.oxygen ?? "--");
  setText("coValue", data.measurements.co ?? "--");
  setText("no2Value", data.measurements.no2 ?? "--");
  setText("nh3Value", data.measurements.nh3 ?? "--");
  setText("alarmText", JSON.stringify({ alarms: data.alarms, faults: data.sensor_faults }));
  if (data.require_password_change) {
    setText("configMessage", "First run active. Set a new password before leaving configuration mode.");
  }
}

async function login() {
  const username = document.getElementById("username").value;
  const password = document.getElementById("password").value;
  try {
    const response = await api("/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    state.token = response.token;
    document.getElementById("loginPanel").classList.add("hidden");
    document.getElementById("appPanel").classList.remove("hidden");
    await refreshConfig();
    await refreshMeasurements();
    state.pollHandle = setInterval(refreshMeasurements, 1000);
  } catch (error) {
    setText("loginMessage", error.message);
  }
}

async function saveConfig() {
  const payload = {
    system: {
      device_name: document.getElementById("deviceName").value,
    },
    web: {
      port: document.getElementById("webPort").value,
      password: document.getElementById("newPassword").value,
    },
    network: {
      mode: document.getElementById("networkMode").value,
      static_ip: document.getElementById("staticIp").value,
      gateway: document.getElementById("gateway").value,
      dns: document.getElementById("dns").value,
    },
    alarms: {
      oxygen_low: document.getElementById("oxygenLow").value,
      oxygen_high: document.getElementById("oxygenHigh").value,
      co_high: document.getElementById("coHigh").value,
    },
  };
  try {
    await api("/api/config", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    document.getElementById("newPassword").value = "";
    setText("configMessage", "Configuration saved.");
    await refreshConfig();
  } catch (error) {
    setText("configMessage", error.message);
  }
}

async function rebootDevice() {
  try {
    await api("/api/reboot", { method: "POST", body: "{}" });
    setText("configMessage", "Reboot requested.");
  } catch (error) {
    setText("configMessage", error.message);
  }
}

document.getElementById("loginBtn").addEventListener("click", login);
document.getElementById("saveConfigBtn").addEventListener("click", saveConfig);
document.getElementById("rebootBtn").addEventListener("click", rebootDevice);
