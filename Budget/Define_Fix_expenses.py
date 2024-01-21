class Expense:
    def __init__(self, name, category, amount, due_date):
        self.name = name
        self.category = category
        self.amount = amount
        self.due_date = due_date
        self.comments = []

    def display_expense(self):
        print(f"Expense: {self.name}")
        print(f"Category: {self.category}")
        print(f"Amount: ${self.amount}")
        print(f"Due Date: {self.due_date}")
        if self.comments:
            print("Comments:")
            for comment in self.comments:
                print(f"  - {comment}")
        print("------------")

    def add_comment(self, comment):
        self.comments.append(comment)


def add_expense():
    name = input("Enter expense name: ")
    category = input("Enter expense category: ")
    amount = float(input("Enter expense amount: $"))
    due_date = input("Enter due date (MM/DD/YYYY): ")

    expense = Expense(name, category, amount, due_date)

    comment = input("Add a comment to the expense (press enter to skip): ")
    if comment:
        expense.add_comment(comment)

    return expense


def list_all_expenses(expenses):
    if not expenses:
        print("No expenses to display.")
    else:
        print("\n--- All Expenses ---")
        for expense in expenses:
            expense.display_expense()


def main():
    expenses = []

    while True:
        print("\n1. Add Expense")
        print("2. List Expenses")
        print("3. List All Expenses with Comments")
        print("4. Exit")

        choice = input("Enter your choice (1/2/3/4): ")

        if choice == "1":
            new_expense = add_expense()
            expenses.append(new_expense)
            print("Expense added successfully!")
        elif choice == "2":
            if not expenses:
                print("No expenses to display.")
            else:
                print("\n--- Monthly Fixed Expenses ---")
                for expense in expenses:
                    expense.display_expense()
        elif choice == "3":
            list_all_expenses(expenses)
        elif choice == "4":
            print("Exiting program. Goodbye!")
            break
        else:
            print("Invalid choice. Please enter 1, 2, 3, or 4.")

if __name__ == "__main__":
    main()
  
