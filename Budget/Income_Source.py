class IncomeSource:
    def __init__(self, name, contact, amount, date):
        self.name = name
        self.contact = contact
        self.amount = amount
        self.date = date

def add_income_source():
    name = input("Enter the name of the income source: ")
    contact = input("Enter the phone number/account number: ")

    # Validating the amount input
    while True:
        try:
            amount = float(input("Enter the income amount: "))
            break
        except ValueError:
            print("Invalid input. Please enter a valid number.")

    # Validating the date input
    while True:
        date = input("Enter the date (YYYY-MM-DD): ")
        if len(date) == 10 and date[4] == date[7] == '-' and date[:4].isdigit() and date[5:7].isdigit() and date[8:].isdigit():
            break
        else:
            print("Invalid date format. Please use YYYY-MM-DD.")

    source = IncomeSource(name, contact, amount, date)
    return source

def list_income_sources(income_sources):
    if not income_sources:
        print("No income sources added yet.")
    else:
        print("\nIncome Sources:")
        for idx, source in enumerate(income_sources, start=1):
            print(f"{idx}. {source.name} - {source.contact} - ${source.amount:.2f} - {source.date}")

def main():
    income_sources = []

    while True:
        print("\n1. Add Income Source\n2. List Income Sources\n3. Exit")
        choice = input("Select an option (1/2/3): ")

        if choice == "1":
            source = add_income_source()
            income_sources.append(source)
            print("Income source added successfully!")

        elif choice == "2":
            list_income_sources(income_sources)

        elif choice == "3":
            print("Exiting the program.")
            break

        else:
            print("Invalid choice. Please choose a valid option.")

if __name__ == "__main__":
    main()
  
