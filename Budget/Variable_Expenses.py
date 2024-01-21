class Expense:
    def __init__(self, name, amount, category, comment=""):
        self.name = name
        self.amount = amount
        self.category = category
        self.comment = comment

class ExpenseTracker:
    def __init__(self):
        self.expenses = []

    def add_expense(self, expense):
        self.expenses.append(expense)

    def display_expenses(self):
        if not self.expenses:
            print("No expenses to display.")
            return

        print("\nList of Monthly Variable Expenses:")
        for index, expense in enumerate(self.expenses, start=1):
            print(f"{index}. Name: {expense.name}, Amount: {expense.amount}$, Category: {expense.category}, Comment: {expense.comment}")

def get_user_input():
    name = input("Enter expense name: ")
    amount = float(input("Enter expense amount: "))
    category = input("Enter expense category: ")
    comment = input("Enter any comments for the expense (optional): ")
    return Expense(name, amount, category, comment)

def main():
    expense_tracker = ExpenseTracker()

    while True:
        print("\n--- Expense Tracker Menu ---")
        print("1. Add Expense")
        print("2. Display Expenses")
        print("3. Exit")

        choice = input("Enter your choice (1, 2, or 3): ")

        if choice == "1":
            expense = get_user_input()
            expense_tracker.add_expense(expense)
            print("Expense added successfully!")
        elif choice == "2":
            expense_tracker.display_expenses()
        elif choice == "3":
            print("Exiting Expense Tracker. Goodbye!")
            break
        else:
            print("Invalid choice. Please enter 1, 2, or 3.")

if __name__ == "__main__":
    main()
  
