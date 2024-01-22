import statistics

def calculate_liquidity_reserve(expenses_history):
    if len(expenses_history) < 6:
        # Extrapolate data using statistical functions
        if len(expenses_history) > 1:
            average_expense = statistics.mean(expenses_history)
            extrapolated_expenses = [average_expense] * (6 - len(expenses_history))
            expenses_history.extend(extrapolated_expenses)
        else:
            print("Insufficient data to extrapolate. Please provide more information.")
            return None

    average_monthly_expenses = sum(expenses_history) / len(expenses_history)
    liquidity_reserve = 6 * average_monthly_expenses

    return liquidity_reserve

def main():
    # Get user input for monthly expenses
    user_expenses = []
    for month in range(1, 7):
        while True:
            try:
                expense = float(input(f"Enter expenses for month {month}: "))
                user_expenses.append(expense)
                break
            except ValueError:
                print("Invalid input. Please enter a valid number.")

    # Calculate liquidity reserve
    liquidity_reserve = calculate_liquidity_reserve(user_expenses)

    # Display the result
    if liquidity_reserve is not None:
        print(f"\nThe recommended liquidity reserve is ${liquidity_reserve:.2f}")

if __name__ == "__main__":
    main()
  
