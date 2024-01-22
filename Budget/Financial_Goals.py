class FinancialGoals:
    def __init__(self):
        self.goals = [
            "Save 20% of monthly income for emergency fund",
            "Invest in a diversified portfolio with a target annual return",
            "Pay off high-interest debts in the next 12 months",
            "Increase side income by 15% by year-end",
            "Save for a major purchase (e.g., house or car) within three years",
            "Establish and stick to a monthly budget",
            "Increase retirement savings contribution to 10% within two years",
            "Acquire new skills or certifications for career and financial growth",
            "Create a plan to diversify income sources",
            "Save for additional education or training to boost career prospects",
            "Develop a financial succession plan for family security",
            "Save for specific experiences or vacations without going into debt",
            "Maximize use of available tax advantages, such as retirement accounts",
            "Review and optimize monthly expenses for additional savings",
            "Build a six-month emergency fund for financial stability"
        ]
        self.selected_goals = []

    def choose_goals(self):
        print("Choose financial goals by entering the corresponding numbers:")
        for i, goal in enumerate(self.goals, 1):
            print(f"{i}. {goal}")

        selections = input("Enter goal numbers separated by commas (e.g., 1, 3, 5): ")
        selected_indices = [int(index.strip()) - 1 for index in selections.split(',')]

        self.selected_goals = [self.goals[index] for index in selected_indices]

    def display_selected_goals(self):
        if not self.selected_goals:
            print("No goals selected.")
        else:
            print("Selected financial goals:")
            for goal in self.selected_goals:
                print(f"- {goal}")


# Example usage:
financial_goals_tracker = FinancialGoals()
financial_goals_tracker.choose_goals()
financial_goals_tracker.display_selected_goals()
