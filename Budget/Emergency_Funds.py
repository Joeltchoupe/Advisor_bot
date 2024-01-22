class EmergencyFund:
    def __init__(self, name, initial_amount=0, category=None):
        self.name = name
        self.balance = initial_amount
        self.category = category

    def deposit(self, amount):
        """
        Deposit funds into the emergency fund.

        :param amount: The amount to deposit.
        :return: A string indicating the deposit transaction.
        """
        self.balance += amount
        return f"Deposited ${amount} into {self.name}. New balance: ${self.balance}"

    def withdraw(self, amount, expense_category):
        """
        Withdraw funds from the emergency fund, subject to category validation.

        :param amount: The amount to withdraw.
        :param expense_category: The category of the expense.
        :return: A string indicating the withdrawal transaction or an error message.
        """
        if expense_category == self.category:
            if amount <= self.balance:
                self.balance -= amount
                return f"Withdrew ${amount} for {expense_category} from {self.name}. New balance: ${self.balance}"
            else:
                return f"Insufficient funds in {self.name} for {expense_category}. Withdrawal not possible."
        else:
            return f"Expense category '{expense_category}' does not match the category of {self.name}. Withdrawal not allowed."

    def check_balance(self):
        """
        Check the current balance of the emergency fund.

        :return: A string indicating the current balance.
        """
        return f"Current balance in {self.name}: ${self.balance}"

# Example usage:
emergency_fund = EmergencyFund(name="Emergency Fund", initial_amount=1000, category="Medical")
print(emergency_fund.check_balance())
print(emergency_fund.deposit(500))
print(emergency_fund.withdraw(200, "Medical"))
print(emergency_fund.withdraw(300, "Housing"))
print(emergency_fund.check_balance())
