class Category:
    def __init__(self, max_spending):
        self.max_spending = max_spending
        self.expenses = []

    def add_expense(self, amount):
        self.expenses.append(amount)

    def calculate_spending(self):
        return sum(self.expenses)


class Budget:
    def __init__(self):
        self.categories = {"Housing": Category(1000),
                           "Transportation": Category(500),
                           "Food": Category(300),
                           "Health": Category(200),
                           "Entertainment": Category(150),
                           "Debt Payments": Category(0),
                           "Personal Care": Category(50),
                           "Insurance": Category(100),
                           "Savings": Category(200),
                           "Clothing": Category(100)}

    def add_category(self, category_name, max_spending):
        if category_name not in self.categories:
            self.categories[category_name] = Category(max_spending)
            print(f"{category_name} category added successfully with a max spending limit of {max_spending}.")
        else:
            print(f"Error: {category_name} already exists in the budget.")

    def show_categories(self):
        print("Available Categories:")
        for category in self.categories:
            print(f"- {category}")

    def show_budget(self):
        print("\nCurrent Budget:")
        for category, data in self.categories.items():
            spending = data.calculate_spending()
            print(f"{category}: Max Spending - {data.max_spending}, Actual Spending - {spending}")

    def check_alert(self, category_name):
        if category_name in self.categories:
            category = self.categories[category_name]
            spending = category.calculate_spending()
            if spending >= category.max_spending:
                print(f"Alert: Maximum spending limit reached for {category_name} category!")
        else:
            print(f"Error: {category_name} does not exist in the budget.")

# Example Usage:
user_budget = Budget()

# Adding expenses to trigger an alert
user_budget.categories["Food"].add_expense(200)
user_budget.categories["Food"].add_expense(150)

# Checking alerts
user_budget.check_alert("Food")
