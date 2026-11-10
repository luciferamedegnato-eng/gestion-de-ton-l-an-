const globalNotice = document.querySelector("#notice");
const transactionDialog = document.querySelector("#transaction-dialog");
const profileDialog = document.querySelector("#profile-dialog");
let transactionType = "expense";
let installPrompt = null;
let profileName = "";
let profileRequired = false;

const categories = [
  ["Alimentation", "⌂"],
  ["Transport", "↗"],
  ["Logement", "⌂"],
  ["Santé", "✚"],
  ["Loisirs", "✳"],
  ["Autre", "✦"],
];

function readCookie(name) {
  const prefix = `${name}=`;
  const entry = document.cookie.split("; ").find((cookie) => cookie.startsWith(prefix));
  return entry ? decodeURIComponent(entry.slice(prefix.length)) : "";
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (options.method && options.method !== "GET") {
    headers.set("X-CSRF-Token", readCookie("depenses_csrf"));
  }
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    throw new Error(data?.detail || "Une erreur est survenue. Réessayez.");
  }
  return data;
}

function showNotice(message, success = false) {
  globalNotice.textContent = message;
  globalNotice.classList.toggle("success", success);
  globalNotice.hidden = false;
}

function clearNotices() {
  globalNotice.hidden = true;
}

function greeting() {
  const hour = new Date().getHours();
  const salutation = hour < 12 ? "Bonjour" : hour < 18 ? "Bon après-midi" : "Bonsoir";
  return profileName ? `${salutation}, ${profileName} !` : `${salutation} !`;
}

function showApp() {
  document.querySelector("#page-title").textContent = greeting();
  document.querySelector("#today-label").textContent = new Intl.DateTimeFormat("fr-FR", {
    weekday: "short",
    day: "numeric",
    month: "short",
  }).format(new Date());
  document.querySelector("#profile-name").textContent = profileName || "Ajouter mon nom";
  document.querySelector("#profile-avatar").textContent =
    profileName ? Array.from(profileName)[0].toLocaleUpperCase("fr-FR") : "?";
  loadDashboard();
}

function openProfileDialog(required = false) {
  profileRequired = required;
  document.querySelector("#profile-title").textContent = required
    ? "Comment vous appelez-vous ?"
    : "Modifier mon nom";
  document.querySelector("#profile-name-input").value = profileName;
  document.querySelector("#close-profile-dialog").hidden = required;
  document.querySelector("#save-profile-button").innerHTML = required
    ? 'Enregistrer mon nom <span>→</span>'
    : 'Enregistrer <span>→</span>';
  profileDialog.showModal();
  document.querySelector("#profile-name-input").focus();
}

function formatMoney(amountXof) {
  return `${new Intl.NumberFormat("fr-FR", {
    maximumFractionDigits: 0,
  }).format(amountXof || 0)} FCFA`;
}

function formatDate(value) {
  const [year, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("fr-FR", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(year, month - 1, day));
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function transactionRow(transaction) {
  const isIncome = transaction.type === "income";
  const category = transaction.category || (isIncome ? "Revenu" : "Dépense");
  const sign = isIncome ? "+" : "−";
  return `<div class="transaction-row">
    <div class="transaction-main">
      <span class="transaction-symbol ${isIncome ? "" : "expense"}">${isIncome ? "↙" : "↗"}</span>
      <span class="transaction-description">${escapeHtml(transaction.description)}</span>
    </div>
    <span class="transaction-category">${escapeHtml(category)}</span>
    <span class="transaction-date">${formatDate(transaction.operation_date)}</span>
    <span class="transaction-amount ${isIncome ? "income" : ""}">${sign}${formatMoney(transaction.amount_xof)}</span>
  </div>`;
}

function renderTransactions(element, transactions) {
  element.innerHTML = transactions.length
    ? `<div class="transaction-list">${transactions.map(transactionRow).join("")}</div>`
    : '<div class="empty-state">Vos opérations apparaîtront ici. Commencez quand vous le souhaitez.</div>';
}

function renderCategories(element, items) {
  if (!items.length) {
    element.innerHTML = '<div class="empty-state">Ajoutez une dépense pour découvrir vos catégories.</div>';
    return;
  }
  const maxAmount = Math.max(...items.map((item) => item.expense_xof));
  element.innerHTML = items.map((item) => {
    const category = categories.find(([name]) => name === item.category);
    const width = Math.max(4, Math.round((item.expense_xof / maxAmount) * 100));
    return `<div class="category-row">
      <span class="category-icon">${category?.[1] || "✦"}</span>
      <span class="category-info">
        <span class="category-name"><span>${escapeHtml(item.category || "Autre")}</span><span class="category-amount">${formatMoney(item.expense_xof)}</span></span>
        <span class="category-track"><span class="category-fill" style="display:block;width:${width}%"></span></span>
      </span>
      <span></span>
    </div>`;
  }).join("");
}

function periodLabel(value, period) {
  if (period === "month") {
    const [year, month] = value.split("-").map(Number);
    return new Intl.DateTimeFormat("fr-FR", { month: "short" }).format(new Date(year, month - 1, 1));
  }
  if (period === "day") {
    const [, month, day] = value.split("-");
    return `${day}/${month}`;
  }
  return value;
}

function renderChart(periods, period) {
  const chart = document.querySelector("#cashflow-chart");
  const entries = periods.slice(0, 6).reverse();
  if (!entries.length) {
    chart.innerHTML = '<div class="empty-state">Vos chiffres prendront forme au fil des opérations.</div>';
    return;
  }
  const maxValue = Math.max(
    1,
    ...entries.flatMap((entry) => [entry.income_xof, entry.expense_xof]),
  );
  chart.innerHTML = entries.map((entry) => {
    const incomeHeight = Math.max(3, (entry.income_xof / maxValue) * 88);
    const expenseHeight = Math.max(3, (entry.expense_xof / maxValue) * 88);
    return `<div class="chart-column">
      <div class="chart-bars">
        <span class="chart-bar income" style="height:${incomeHeight}%" title="Revenus : ${formatMoney(entry.income_xof)}"></span>
        <span class="chart-bar expense" style="height:${expenseHeight}%" title="Dépenses : ${formatMoney(entry.expense_xof)}"></span>
      </div>
      <span class="chart-label">${periodLabel(entry.period, period)}</span>
    </div>`;
  }).join("");
}

function selectTransactionType(type) {
  transactionType = type;
  document.querySelectorAll(".type-option").forEach((button) => {
    button.classList.toggle("selected", button.dataset.type === type);
  });
  const isExpense = type === "expense";
  document.querySelector("#category-field").hidden = !isExpense;
  document.querySelector("#transaction-category").required = isExpense;
}

async function loadDashboard() {
  try {
    const [summary, statistics] = await Promise.all([
      api("/api/dashboard"),
      api("/api/statistics?period=month"),
    ]);
    document.querySelector("#balance-total").textContent = formatMoney(summary.balance_xof);
    document.querySelector("#month-income").textContent = formatMoney(summary.month_income_xof);
    document.querySelector("#month-expenses").textContent = formatMoney(summary.month_expense_xof);
    renderTransactions(document.querySelector("#recent-transactions"), summary.transactions);
    renderCategories(document.querySelector("#category-list"), statistics.categories);
    renderChart(statistics.periods, "month");
  } catch (error) {
    showNotice(error.message);
  }
}

function navigateTo(page) {
  const allowedPages = ["dashboard", "transactions", "analytics", "import"];
  const selected = allowedPages.includes(page) ? page : "dashboard";
  document.querySelectorAll(".page-content").forEach((section) => {
    section.hidden = section.id !== `${selected}-page`;
  });
  document.querySelectorAll(".nav-link[data-page]").forEach((button) => {
    button.classList.toggle("active", button.dataset.page === selected);
  });
  const title = {
    dashboard: greeting(),
    transactions: "Vos transactions",
    analytics: "Vos analyses",
    import: "Importer mes données",
  }[selected];
  document.querySelector("#page-title").textContent = title;
  document.querySelector("#sidebar").classList.remove("open");
  clearNotices();
  if (selected === "transactions") loadTransactions();
  if (selected === "analytics") loadAnalytics();
}

async function loadTransactions() {
  try {
    renderTransactions(document.querySelector("#all-transactions"), await api("/api/transactions"));
  } catch (error) {
    showNotice(error.message);
  }
}

async function loadAnalytics() {
  const period = document.querySelector("#analytics-period").value;
  try {
    const data = await api(`/api/statistics?period=${encodeURIComponent(period)}`);
    const income = data.periods.reduce((sum, item) => sum + item.income_xof, 0);
    const expenses = data.periods.reduce((sum, item) => sum + item.expense_xof, 0);
    document.querySelector("#analytics-summary").innerHTML = `
      <article class="summary-card"><div class="card-heading">Revenus analysés</div><strong class="metric-amount">${formatMoney(income)}</strong><span class="card-caption">Sur les périodes disponibles</span></article>
      <article class="summary-card"><div class="card-heading">Dépenses analysées</div><strong class="metric-amount">${formatMoney(expenses)}</strong><span class="card-caption">Sur les périodes disponibles</span></article>
      <article class="summary-card"><div class="card-heading">Différence nette</div><strong class="metric-amount">${formatMoney(income - expenses)}</strong><span class="card-caption">Revenus moins dépenses</span></article>`;
    renderCategories(document.querySelector("#analytics-categories"), data.categories);
    document.querySelector("#period-table").innerHTML = data.periods.length
      ? `<div class="period-list">${data.periods.map((item) => `<div class="period-row">
          <span>${periodLabel(item.period, period)}</span>
          <span>${formatMoney(item.income_xof)}</span>
          <strong class="expense-value">${formatMoney(item.expense_xof)}</strong>
        </div>`).join("")}</div>`
      : '<div class="empty-state">Aucune donnée sur cette période pour le moment.</div>';
  } catch (error) {
    showNotice(error.message);
  }
}

async function initialize() {
  try {
    await api("/api/csrf");
    const profile = await api("/api/profile");
    profileName = profile.name;
    showApp();
    if (!profileName) openProfileDialog(true);
  } catch (error) {
    showNotice(`Impossible de démarrer l’application : ${error.message}`);
  }
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      showNotice("L’installation hors ligne n’est pas disponible pour le moment.");
    });
  }
}

document.querySelector("#profile-button").addEventListener("click", () => {
  openProfileDialog();
});

document.querySelector("#close-profile-dialog").addEventListener("click", () => {
  profileDialog.close();
});

profileDialog.addEventListener("cancel", (event) => {
  if (profileRequired) event.preventDefault();
});

document.querySelector("#profile-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.querySelector("#save-profile-button");
  button.disabled = true;
  try {
    const result = await api("/api/profile", {
      method: "PUT",
      body: JSON.stringify({
        name: document.querySelector("#profile-name-input").value,
      }),
    });
    profileName = result.name;
    profileRequired = false;
    profileDialog.close();
    showApp();
    showNotice("Votre nom est enregistré sur cet appareil.", true);
  } catch (error) {
    showNotice(error.message);
  } finally {
    button.disabled = false;
  }
});

document.querySelectorAll("[data-page]").forEach((button) => {
  button.addEventListener("click", (event) => {
    event.preventDefault();
    navigateTo(button.dataset.page);
  });
});

document.querySelector("#menu-button").addEventListener("click", () => {
  document.querySelector("#sidebar").classList.toggle("open");
});

document.querySelector("#add-transaction-button").addEventListener("click", () => {
  document.querySelector("#transaction-form").reset();
  selectTransactionType("expense");
  const now = new Date();
  document.querySelector("#transaction-date").value = [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");
  transactionDialog.showModal();
});

document.querySelector("#close-dialog").addEventListener("click", () => transactionDialog.close());

document.querySelectorAll(".type-option").forEach((button) => {
  button.addEventListener("click", () => {
    selectTransactionType(button.dataset.type);
  });
});

document.querySelector("#transaction-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const payload = {
    amount: data.get("amount"),
    type: transactionType,
    description: data.get("description"),
    category: transactionType === "expense" ? data.get("category") : null,
    operation_date: data.get("operation_date"),
  };
  try {
    await api("/api/transactions", { method: "POST", body: JSON.stringify(payload) });
    transactionDialog.close();
    form.reset();
    selectTransactionType("expense");
    clearNotices();
    await loadDashboard();
    if (!document.querySelector("#transactions-page").hidden) await loadTransactions();
    if (!document.querySelector("#analytics-page").hidden) await loadAnalytics();
    showNotice("Opération enregistrée. Elle est bien dans votre espace.", true);
  } catch (error) {
    showNotice(error.message);
  }
});

document.querySelector("#chart-period").addEventListener("change", async (event) => {
  try {
    const data = await api(`/api/statistics?period=${event.currentTarget.value}`);
    renderChart(data.periods, event.currentTarget.value);
  } catch (error) {
    showNotice(error.message);
  }
});

document.querySelector("#analytics-period").addEventListener("change", loadAnalytics);

document.querySelector("#import-file").addEventListener("change", (event) => {
  document.querySelector("#file-name").textContent =
    event.currentTarget.files[0]?.name || "Aucun fichier sélectionné";
});

document.querySelector("#import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = document.querySelector("#import-file").files[0];
  if (!file) return;
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  const form = new FormData();
  form.append("file", file);
  try {
    const result = await api("/api/import", { method: "POST", body: form });
    showNotice(
      result.already_imported
        ? "Cette base a déjà été importée."
        : `${result.imported} opération(s) importée(s).`,
      true,
    );
    await loadDashboard();
  } catch (error) {
    showNotice(error.message);
  } finally {
    button.disabled = false;
  }
});

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  installPrompt = event;
  document.querySelector("#install-button").hidden = false;
});

document.querySelector("#install-button").addEventListener("click", async () => {
  if (!installPrompt) return;
  installPrompt.prompt();
  await installPrompt.userChoice;
  installPrompt = null;
  document.querySelector("#install-button").hidden = true;
});

initialize();
