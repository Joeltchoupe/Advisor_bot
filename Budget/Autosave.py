class BankAccount:
    def __init__(self, balance=0):
        self.balance = balance

    def deposit(self, amount):
        self.balance += amount

    def withdraw(self, amount):
        self.balance -= amount

def main():
    # Create a user's main account and savings account
    main_account = BankAccount()
    savings_account = BankAccount()

    while True:
        try:
            transaction_amount = float(input("Enter transaction amount: $"))
            if transaction_amount <= 0:
                print("Invalid amount. Please enter a positive value.")
                continue

            # Calculate 20% for savings and deduct from the main account
            savings_amount = transaction_amount * 0.2
            main_account.withdraw(transaction_amount)
            savings_account.deposit(savings_amount)

            print(f"Transaction successful!\nMain Account Balance: ${main_account.balance}\nSavings Account Balance: ${savings_account.balance}")

        except ValueError:
            print("Invalid input. Please enter a numeric value.")

if __name__ == "__main__":
    main()
  
