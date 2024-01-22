class SavingsPlan:
    def __init__(self, name, initial_balance, monthly_contribution, interest_rate, duration):
        self.name = name
        self.balance = initial_balance
        self.monthly_contribution = monthly_contribution
        self.interest_rate = interest_rate
        self.duration = duration

    def calculate_balance(self):
        for month in range(1, self.duration + 1):
            interest_earned = (self.balance + self.monthly_contribution) * (self.interest_rate / 12)
            self.balance = self.balance + self.monthly_contribution + interest_earned
            print(f"Month {month}: Balance = {self.balance:.2f}")

    def display_summary(self):
        print(f"\nSavings Plan Summary for {self.name}:")
        print(f"Initial Balance: {self.balance:.2f}")
        print(f"Monthly Contribution: {self.monthly_contribution:.2f}")
        print(f"Annual Interest Rate: {self.interest_rate * 100:.2f}%")
        print(f"Duration (months): {self.duration}")

    def contribution_schedule(self, contribution_period):
        equivalent_monthly_contribution = self.monthly_contribution

        if contribution_period != 'monthly':
            equivalent_monthly_contribution = self.monthly_contribution * self.duration / self._get_period_multiplier(contribution_period)

        return equivalent_monthly_contribution

    def _get_period_multiplier(self, contribution_period):
        period_multipliers = {'yearly': 12, 'quarterly': 3, 'semi-annually': 6}
        return period_multipliers.get(contribution_period, 1)

# Example usage:
name = input("Enter the name of your savings plan: ")
initial_balance = float(input("Enter the initial balance: "))
monthly_contribution = float(input("Enter the monthly contribution amount: "))
interest_rate = float(input("Enter the annual interest rate (as a decimal): "))
duration = int(input("Enter the duration of the savings plan in months: "))
contribution_period = input("Enter the contribution period (monthly/yearly/quarterly/semi-annually): ")

my_savings_plan = SavingsPlan(name, initial_balance, monthly_contribution, interest_rate, duration)
equivalent_monthly_contribution = my_savings_plan.contribution_schedule(contribution_period)

my_savings_plan.monthly_contribution = equivalent_monthly_contribution
my_savings_plan.calculate_balance()
my_savings_plan.display_summary()
