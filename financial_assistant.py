#!/usr/bin/env python3
"""
Personal Financial Assistant - MVP
A self-hosted web app for intentional daily spending tracking with income splitting,
savings goals, and recurring expenses management.
"""

from flask import (
    Flask,
    render_template_string,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
)
import sqlite3
from datetime import datetime, timedelta, date
from decimal import Decimal, ROUND_HALF_UP
import json

# Fix SQLite date handling for Python 3.13+
sqlite3.register_adapter(date, lambda d: d.isoformat())
sqlite3.register_converter("DATE", lambda s: datetime.fromisoformat(s.decode()).date())

app = Flask(__name__)
app.secret_key = "your-secret-key-change-this"


# Database initialization
def init_db():
    conn = sqlite3.connect("financial_assistant.db")
    c = conn.cursor()

    # Pay periods table
    c.execute(
        """CREATE TABLE IF NOT EXISTS pay_periods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        amount DECIMAL(10,2) NOT NULL,
        start_date DATE NOT NULL,
        end_date DATE NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    )

    # Savings goals table
    c.execute(
        """CREATE TABLE IF NOT EXISTS savings_goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        target_amount DECIMAL(10,2) NOT NULL,
        current_amount DECIMAL(10,2) DEFAULT 0,
        target_date DATE,
        monthly_contribution DECIMAL(10,2) DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    )

    # Recurring expenses table
    c.execute(
        """CREATE TABLE IF NOT EXISTS recurring_expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        amount DECIMAL(10,2) NOT NULL,
        frequency TEXT NOT NULL, -- 'monthly', 'weekly', 'yearly'
        next_due DATE NOT NULL,
        category TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    )

    # Daily transactions table
    c.execute(
        """CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date DATE NOT NULL,
        amount DECIMAL(10,2) NOT NULL,
        category TEXT,
        description TEXT,
        transaction_type TEXT DEFAULT 'expense', -- 'expense', 'savings_contribution'
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    )

    # Daily budgets table
    c.execute(
        """CREATE TABLE IF NOT EXISTS daily_budgets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date DATE UNIQUE NOT NULL,
        allocated_limit DECIMAL(10,2) NOT NULL,
        confirmed_limit DECIMAL(10,2),
        actual_spent DECIMAL(10,2) DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""
    )

    conn.commit()
    conn.close()


def get_db():
    conn = sqlite3.connect("financial_assistant.db")
    conn.row_factory = sqlite3.Row
    return conn


def calculate_daily_limit():
    """Calculate suggested daily spending limit based on current financial situation"""
    conn = get_db()
    today = date.today()

    # Get current pay period
    current_period = conn.execute(
        "SELECT * FROM pay_periods WHERE start_date <= ? AND end_date >= ? ORDER BY start_date DESC LIMIT 1",
        (today, today),
    ).fetchone()

    if not current_period:
        conn.close()
        return 0

    # Calculate remaining money in period
    period_start = datetime.strptime(current_period["start_date"], "%Y-%m-%d").date()
    period_end = datetime.strptime(current_period["end_date"], "%Y-%m-%d").date()
    days_remaining = (period_end - today).days + 1

    # Get total spent this period
    total_spent = conn.execute(
        'SELECT COALESCE(SUM(amount), 0) as total FROM transactions WHERE date >= ? AND date <= ? AND transaction_type = "expense"',
        (period_start, today),
    ).fetchone()["total"]

    # Get upcoming recurring expenses for this period
    upcoming_bills = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) as total FROM recurring_expenses WHERE next_due >= ? AND next_due <= ?",
        (today, period_end),
    ).fetchone()["total"]

    # Get planned savings contributions
    planned_savings = conn.execute(
        "SELECT COALESCE(SUM(monthly_contribution), 0) as total FROM savings_goals"
    ).fetchone()["total"]

    # Calculate available for daily spending
    remaining_income = float(current_period["amount"]) - float(total_spent)
    available_for_spending = (
        remaining_income - float(upcoming_bills) - float(planned_savings)
    )

    if days_remaining <= 0:
        daily_limit = 0
    else:
        daily_limit = max(0, available_for_spending / days_remaining)

    conn.close()
    return round(daily_limit, 2)


# HTML Templates
MAIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Financial Assistant</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; background: #f8f9fa; }
        .container { max-width: 800px; margin: 0 auto; padding: 20px; }
        .card { background: white; border-radius: 8px; padding: 20px; margin: 20px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .nav { display: flex; gap: 20px; margin-bottom: 20px; }
        .nav a { text-decoration: none; color: #007bff; font-weight: 500; }
        .nav a:hover { text-decoration: underline; }
        .progress-bar { width: 100%; height: 20px; background: #e9ecef; border-radius: 10px; overflow: hidden; margin: 10px 0; }
        .progress-fill { height: 100%; background: linear-gradient(90deg, #28a745, #20c997); transition: width 0.3s ease; }
        .amount { font-size: 1.2em; font-weight: bold; }
        .amount.positive { color: #28a745; }
        .amount.negative { color: #dc3545; }
        .amount.warning { color: #ffc107; }
        .btn { background: #007bff; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; text-decoration: none; display: inline-block; }
        .btn:hover { background: #0056b3; }
        .btn.danger { background: #dc3545; }
        .btn.success { background: #28a745; }
        .form-group { margin: 15px 0; }
        .form-group label { display: block; margin-bottom: 5px; font-weight: 500; }
        .form-group input, .form-group select, .form-group textarea { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; }
        .quick-amounts { display: flex; gap: 10px; margin: 10px 0; flex-wrap: wrap; }
        .quick-amounts button { background: #f8f9fa; border: 1px solid #ddd; padding: 8px 12px; border-radius: 4px; cursor: pointer; }
        .quick-amounts button:hover { background: #e9ecef; }
        .flash { padding: 10px; margin: 10px 0; border-radius: 4px; }
        .flash.success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .flash.error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>💰 Financial Assistant</h1>
        <nav class="nav">
            <a href="/">Dashboard</a>
            <a href="/add-transaction">Add Transaction</a>
            <a href="/savings-goals">Savings Goals</a>
            <a href="/recurring-expenses">Recurring Expenses</a>
            <a href="/pay-periods">Pay Periods</a>
        </nav>
        
        {% for message in get_flashed_messages() %}
            <div class="flash success">{{ message }}</div>
        {% endfor %}
        
        {% block content %}{% endblock %}
    </div>
    
    <script>
        function setQuickAmount(amount) {
            document.getElementById('amount').value = amount;
        }
        
        function confirmDailyLimit() {
            const suggested = parseFloat(document.getElementById('suggested-limit').textContent);
            const confirmed = prompt(`Confirm today's spending limit?\nSuggested: ${suggested}`, suggested);
            if (confirmed !== null) {
                fetch('/confirm-daily-limit', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({limit: parseFloat(confirmed)})
                }).then(() => location.reload());
            }
        }
    </script>
</body>
</html>
"""


@app.route("/")
def dashboard():
    conn = get_db()
    today = date.today()

    # Get today's budget info
    daily_budget = conn.execute(
        "SELECT * FROM daily_budgets WHERE date = ?", (today,)
    ).fetchone()

    if daily_budget:
        spent_percentage = min(
            100,
            (
                daily_budget["actual_spent"]
                / (daily_budget["confirmed_limit"] or daily_budget["allocated_limit"])
            )
            * 100,
        )
        remaining = (
            daily_budget["confirmed_limit"] or daily_budget["allocated_limit"]
        ) - daily_budget["actual_spent"]
        daily_budget = dict(daily_budget)
        daily_budget["spent_percentage"] = spent_percentage
        daily_budget["remaining"] = remaining

    # Get current pay period
    current_period = conn.execute(
        "SELECT * FROM pay_periods WHERE start_date <= ? AND end_date >= ? ORDER BY start_date DESC LIMIT 1",
        (today, today),
    ).fetchone()

    days_remaining = 0
    if current_period:
        period_end = datetime.strptime(current_period["end_date"], "%Y-%m-%d").date()
        days_remaining = (period_end - today).days + 1

    # Get savings goals
    savings_goals = conn.execute(
        "SELECT * FROM savings_goals ORDER BY created_at"
    ).fetchall()

    # Get recent transactions
    recent_transactions = conn.execute(
        'SELECT * FROM transactions WHERE transaction_type = "expense" ORDER BY date DESC, created_at DESC LIMIT 5'
    ).fetchall()

    suggested_limit = calculate_daily_limit() if not daily_budget else 0

    conn.close()

    dashboard_content = """
<div class="grid">
    <!-- Today's Spending -->
    <div class="card">
        <h3>📅 Today's Budget</h3>
        {% if daily_budget %}
            <p class="amount {% if daily_budget.remaining >= 0 %}positive{% else %}negative{% endif %}">
                ${{ "%.2f"|format(daily_budget.remaining) }} remaining
            </p>
            <div class="progress-bar">
                <div class="progress-fill" style="width: {{ daily_budget.spent_percentage }}%"></div>
            </div>
            <p>Spent: ${{ "%.2f"|format(daily_budget.actual_spent) }} / ${{ "%.2f"|format(daily_budget.confirmed_limit or daily_budget.allocated_limit) }}</p>
        {% else %}
            <p>No budget set for today</p>
            <p class="amount">Suggested: $<span id="suggested-limit">{{ suggested_limit }}</span></p>
            <button class="btn" onclick="confirmDailyLimit()">Set Today's Limit</button>
        {% endif %}
    </div>
    
    <!-- Current Pay Period -->
    <div class="card">
        <h3>💼 Current Pay Period</h3>
        {% if current_period %}
            <p><strong>${{ "%.2f"|format(current_period.amount) }}</strong> total</p>
            <p>{{ current_period.start_date }} to {{ current_period.end_date }}</p>
            <p>{{ days_remaining }} days remaining</p>
        {% else %}
            <p>No active pay period</p>
            <a href="/pay-periods" class="btn">Add Pay Period</a>
        {% endif %}
    </div>
</div>

<!-- Savings Goals -->
<div class="card">
    <h3>🎯 Savings Goals</h3>
    {% if savings_goals %}
        {% for goal in savings_goals %}
        <div style="margin: 15px 0; padding: 15px; border: 1px solid #ddd; border-radius: 4px;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <h4>{{ goal.name }}</h4>
                <span class="amount">${{ "%.2f"|format(goal.current_amount) }} / ${{ "%.2f"|format(goal.target_amount) }}</span>
            </div>
            <div class="progress-bar">
                <div class="progress-fill" style="width: {{ (goal.current_amount / goal.target_amount * 100) if goal.target_amount > 0 else 0 }}%"></div>
            </div>
            <p>{{ "%.1f"|format((goal.current_amount / goal.target_amount * 100) if goal.target_amount > 0 else 0) }}% complete
            {% if goal.target_date %}
                • Target: {{ goal.target_date }}
            {% endif %}
            </p>
        </div>
        {% endfor %}
    {% else %}
        <p>No savings goals set</p>
        <a href="/savings-goals" class="btn">Add Savings Goal</a>
    {% endif %}
</div>

<!-- Recent Transactions -->
<div class="card">
    <h3>📊 Recent Transactions</h3>
    {% if recent_transactions %}
        {% for transaction in recent_transactions %}
        <div style="display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #eee;">
            <div>
                <strong>{{ transaction.description or transaction.category }}</strong>
                <br><small>{{ transaction.date }}</small>
            </div>
            <span class="amount negative">${{ "%.2f"|format(transaction.amount) }}</span>
        </div>
        {% endfor %}
    {% else %}
        <p>No recent transactions</p>
    {% endif %}
    <a href="/add-transaction" class="btn" style="margin-top: 15px;">Add Transaction</a>
</div>
    """

    return render_template_string(
        MAIN_TEMPLATE.replace("{% block content %}{% endblock %}", dashboard_content),
        daily_budget=daily_budget,
        current_period=current_period,
        days_remaining=days_remaining,
        savings_goals=savings_goals,
        recent_transactions=recent_transactions,
        suggested_limit=suggested_limit,
    )


@app.route("/confirm-daily-limit", methods=["POST"])
def confirm_daily_limit():
    data = request.get_json()
    limit = data.get("limit", 0)
    today = date.today()

    conn = get_db()

    # Check if budget already exists for today
    existing = conn.execute(
        "SELECT id FROM daily_budgets WHERE date = ?", (today,)
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE daily_budgets SET confirmed_limit = ? WHERE date = ?",
            (limit, today),
        )
    else:
        conn.execute(
            "INSERT INTO daily_budgets (date, allocated_limit, confirmed_limit) VALUES (?, ?, ?)",
            (today, limit, limit),
        )

    conn.commit()
    conn.close()

    return {"success": True}


@app.route("/add-transaction", methods=["GET", "POST"])
def add_transaction():
    if request.method == "POST":
        amount = float(request.form["amount"])
        category = request.form["category"]
        description = request.form["description"]
        transaction_date = request.form.get("date", date.today().isoformat())
        transaction_type = request.form.get("type", "expense")

        conn = get_db()

        # Add transaction
        conn.execute(
            "INSERT INTO transactions (date, amount, category, description, transaction_type) VALUES (?, ?, ?, ?, ?)",
            (transaction_date, amount, category, description, transaction_type),
        )

        # Update daily budget spent amount if it's an expense for today
        if (
            transaction_type == "expense"
            and transaction_date == date.today().isoformat()
        ):
            # Ensure daily budget exists
            existing_budget = conn.execute(
                "SELECT id FROM daily_budgets WHERE date = ?", (transaction_date,)
            ).fetchone()
            if not existing_budget:
                suggested_limit = calculate_daily_limit()
                conn.execute(
                    "INSERT INTO daily_budgets (date, allocated_limit) VALUES (?, ?)",
                    (transaction_date, suggested_limit),
                )

            # Update spent amount
            conn.execute(
                'UPDATE daily_budgets SET actual_spent = (SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE date = ? AND transaction_type = "expense") WHERE date = ?',
                (transaction_date, transaction_date),
            )

        # If it's a savings contribution, update the goal
        if transaction_type == "savings_contribution":
            goal_id = request.form.get("goal_id")
            if goal_id:
                conn.execute(
                    "UPDATE savings_goals SET current_amount = current_amount + ? WHERE id = ?",
                    (amount, goal_id),
                )

        conn.commit()
        conn.close()

        flash(f"Added {transaction_type}: ${amount:.2f}")
        return redirect(url_for("dashboard"))

    # Get savings goals for the form
    conn = get_db()
    savings_goals = conn.execute("SELECT * FROM savings_goals ORDER BY name").fetchall()
    conn.close()

    return render_template_string(
        MAIN_TEMPLATE
        + """
{% block content %}
<div class="card">
    <h2>Add Transaction</h2>
    <form method="POST">
        <div class="form-group">
            <label>Type:</label>
            <select name="type" onchange="toggleSavingsGoal()">
                <option value="expense">Expense</option>
                <option value="savings_contribution">Savings Contribution</option>
            </select>
        </div>
        
        <div class="form-group">
            <label>Amount ($):</label>
            <input type="number" name="amount" id="amount" step="0.01" required>
            <div class="quick-amounts">
                <button type="button" onclick="setQuickAmount(5)">$5</button>
                <button type="button" onclick="setQuickAmount(10)">$10</button>
                <button type="button" onclick="setQuickAmount(15)">$15</button>
                <button type="button" onclick="setQuickAmount(25)">$25</button>
                <button type="button" onclick="setQuickAmount(50)">$50</button>
            </div>
        </div>
        
        <div class="form-group">
            <label>Category:</label>
            <input type="text" name="category" placeholder="Food, Gas, Entertainment, etc.">
        </div>
        
        <div class="form-group">
            <label>Description:</label>
            <input type="text" name="description" placeholder="Coffee, Groceries, etc.">
        </div>
        
        <div class="form-group">
            <label>Date:</label>
            <input type="date" name="date" value="{{ today }}">
        </div>
        
        <div class="form-group" id="savings-goal-group" style="display: none;">
            <label>Savings Goal:</label>
            <select name="goal_id">
                <option value="">Select a goal</option>
                {% for goal in savings_goals %}
                <option value="{{ goal.id }}">{{ goal.name }}</option>
                {% endfor %}
            </select>
        </div>
        
        <button type="submit" class="btn">Add Transaction</button>
    </form>
</div>

<script>
function toggleSavingsGoal() {
    const type = document.querySelector('select[name="type"]').value;
    const goalGroup = document.getElementById('savings-goal-group');
    goalGroup.style.display = type === 'savings_contribution' ? 'block' : 'none';
}
</script>
{% endblock %}
    """,
        savings_goals=savings_goals,
        today=date.today().isoformat(),
    )


@app.route("/savings-goals", methods=["GET", "POST"])
def savings_goals():
    conn = get_db()

    if request.method == "POST":
        name = request.form["name"]
        target_amount = float(request.form["target_amount"])
        target_date = request.form.get("target_date") or None
        monthly_contribution = float(request.form.get("monthly_contribution", 0))

        conn.execute(
            "INSERT INTO savings_goals (name, target_amount, target_date, monthly_contribution) VALUES (?, ?, ?, ?)",
            (name, target_amount, target_date, monthly_contribution),
        )
        conn.commit()
        flash(f"Added savings goal: {name}")
        return redirect(url_for("savings_goals"))

    goals = conn.execute("SELECT * FROM savings_goals ORDER BY created_at").fetchall()
    conn.close()

    return render_template_string(
        MAIN_TEMPLATE
        + """
{% block content %}
<div class="card">
    <h2>Add Savings Goal</h2>
    <form method="POST">
        <div class="form-group">
            <label>Goal Name:</label>
            <input type="text" name="name" placeholder="House Down Payment, Emergency Fund, etc." required>
        </div>
        
        <div class="form-group">
            <label>Target Amount ($):</label>
            <input type="number" name="target_amount" step="0.01" required>
        </div>
        
        <div class="form-group">
            <label>Target Date (optional):</label>
            <input type="date" name="target_date">
        </div>
        
        <div class="form-group">
            <label>Monthly Contribution ($):</label>
            <input type="number" name="monthly_contribution" step="0.01" placeholder="0">
        </div>
        
        <button type="submit" class="btn">Add Goal</button>
    </form>
</div>

<div class="card">
    <h2>Your Savings Goals</h2>
    {% if goals %}
        {% for goal in goals %}
        <div style="margin: 20px 0; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                <h3>{{ goal.name }}</h3>
                <span class="amount">${{ "%.2f"|format(goal.current_amount) }} / ${{ "%.2f"|format(goal.target_amount) }}</span>
            </div>
            
            <div class="progress-bar">
                <div class="progress-fill" style="width: {{ (goal.current_amount / goal.target_amount * 100) if goal.target_amount > 0 else 0 }}%"></div>
            </div>
            
            <div style="display: flex; justify-content: space-between; margin-top: 10px;">
                <span>{{ "%.1f"|format((goal.current_amount / goal.target_amount * 100) if goal.target_amount > 0 else 0) }}% complete</span>
                {% if goal.target_date %}
                <span>Target: {{ goal.target_date }}</span>
                {% endif %}
            </div>
            
            {% if goal.monthly_contribution > 0 %}
            <p style="margin-top: 10px; color: #666;">Monthly contribution: ${{ "%.2f"|format(goal.monthly_contribution) }}</p>
            {% endif %}
        </div>
        {% endfor %}
    {% else %}
        <p>No savings goals yet. Add one above!</p>
    {% endif %}
</div>
{% endblock %}
    """,
        goals=goals,
    )


@app.route("/recurring-expenses", methods=["GET", "POST"])
def recurring_expenses():
    conn = get_db()

    if request.method == "POST":
        name = request.form["name"]
        amount = float(request.form["amount"])
        frequency = request.form["frequency"]
        next_due = request.form["next_due"]
        category = request.form.get("category", "")

        conn.execute(
            "INSERT INTO recurring_expenses (name, amount, frequency, next_due, category) VALUES (?, ?, ?, ?, ?)",
            (name, amount, frequency, next_due, category),
        )
        conn.commit()
        flash(f"Added recurring expense: {name}")
        return redirect(url_for("recurring_expenses"))

    expenses = conn.execute(
        "SELECT * FROM recurring_expenses ORDER BY next_due"
    ).fetchall()
    conn.close()

    return render_template_string(
        MAIN_TEMPLATE
        + """
{% block content %}
<div class="card">
    <h2>Add Recurring Expense</h2>
    <form method="POST">
        <div class="form-group">
            <label>Name:</label>
            <input type="text" name="name" placeholder="Rent, Netflix, Car Insurance, etc." required>
        </div>
        
        <div class="form-group">
            <label>Amount ($):</label>
            <input type="number" name="amount" step="0.01" required>
        </div>
        
        <div class="form-group">
            <label>Frequency:</label>
            <select name="frequency" required>
                <option value="monthly">Monthly</option>
                <option value="weekly">Weekly</option>
                <option value="yearly">Yearly</option>
            </select>
        </div>
        
        <div class="form-group">
            <label>Next Due Date:</label>
            <input type="date" name="next_due" required>
        </div>
        
        <div class="form-group">
            <label>Category:</label>
            <input type="text" name="category" placeholder="Housing, Utilities, Subscriptions, etc.">
        </div>
        
        <button type="submit" class="btn">Add Recurring Expense</button>
    </form>
</div>

<div class="card">
    <h2>Your Recurring Expenses</h2>
    {% if expenses %}
        {% for expense in expenses %}
        <div style="display: flex; justify-content: space-between; align-items: center; padding: 15px 0; border-bottom: 1px solid #eee;">
            <div>
                <h4>{{ expense.name }}</h4>
                <p style="color: #666; margin: 5px 0;">{{ expense.frequency|title }} • Next due: {{ expense.next_due }}</p>
                {% if expense.category %}
                <p style="color: #999; font-size: 0.9em;">{{ expense.category }}</p>
                {% endif %}
            </div>
            <span class="amount negative">${{ "%.2f"|format(expense.amount) }}</span>
        </div>
        {% endfor %}
    {% else %}
        <p>No recurring expenses yet. Add one above!</p>
    {% endif %}
</div>
{% endblock %}
    """,
        expenses=expenses,
    )


@app.route("/pay-periods", methods=["GET", "POST"])
def pay_periods():
    conn = get_db()

    if request.method == "POST":
        amount = float(request.form["amount"])
        start_date = request.form["start_date"]
        end_date = request.form["end_date"]

        conn.execute(
            "INSERT INTO pay_periods (amount, start_date, end_date) VALUES (?, ?, ?)",
            (amount, start_date, end_date),
        )
        conn.commit()
        flash(f"Added pay period: ${amount:.2f}")
        return redirect(url_for("pay_periods"))

    periods = conn.execute(
        "SELECT * FROM pay_periods ORDER BY start_date DESC"
    ).fetchall()
    conn.close()

    return render_template_string(
        MAIN_TEMPLATE
        + """
{% block content %}
<div class="card">
    <h2>Add Pay Period</h2>
    <form method="POST">
        <div class="form-group">
            <label>Net Pay Amount ($):</label>
            <input type="number" name="amount" step="0.01" placeholder="Take-home pay after taxes" required>
        </div>
        
        <div class="form-group">
            <label>Period Start Date:</label>
            <input type="date" name="start_date" required>
        </div>
        
        <div class="form-group">
            <label>Period End Date:</label>
            <input type="date" name="end_date" required>
        </div>
        
        <button type="submit" class="btn">Add Pay Period</button>
    </form>
</div>

<div class="card">
    <h2>Pay Period History</h2>
    {% if periods %}
        {% for period in periods %}
        <div style="display: flex; justify-content: space-between; align-items: center; padding: 15px 0; border-bottom: 1px solid #eee;">
            <div>
                <h4>${{ "%.2f"|format(period.amount) }}</h4>
                <p style="color: #666;">{{ period.start_date }} to {{ period.end_date }}</p>
            </div>
            <div style="text-align: right;">
                {% set today = moment().format('YYYY-MM-DD') %}
                {% if period.start_date <= today <= period.end_date %}
                    <span style="background: #28a745; color: white; padding: 4px 8px; border-radius: 4px; font-size: 0.8em;">CURRENT</span>
                {% endif %}
            </div>
        </div>
        {% endfor %}
    {% else %}
        <p>No pay periods yet. Add one above!</p>
    {% endif %}
</div>
{% endblock %}
    """,
        periods=periods,
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=0)
