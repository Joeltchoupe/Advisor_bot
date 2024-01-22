class AnalyseDepenses:
    def __init__(self, depenses_fixes, depenses_variables):
        self.depenses_fixes = depenses_fixes
        self.depenses_variables = depenses_variables

    def calculer_total_depenses_fixes(self):
        return sum(self.depenses_fixes.values())

    def calculer_total_depenses_variables(self):
        return sum(self.depenses_variables.values())

    def calculer_total_depenses_mensuelles(self):
        return self.calculer_total_depenses_fixes() + self.calculer_total_depenses_variables()

    def calculer_ratio_depenses_variables(self):
        total_fixes = self.calculer_total_depenses_fixes()
        total_variables = self.calculer_total_depenses_variables()

        if total_fixes == 0:
            return "N/A"

        return total_variables / total_fixes

# Exemple d'utilisation
depenses_fixes = {
    'loyer': 1200,
    'electricite': 100,
    'eau': 50,
    'internet': 40,
    'assurance': 80
}

depenses_variables = {
    'courses': 300,
    'sorties': 100,
    'loisirs': 50
}

analyseur = AnalyseDepenses(depenses_fixes, depenses_variables)

print(f"Total des dépenses fixes : {analyseur.calculer_total_depenses_fixes()} €")
print(f"Total des dépenses variables : {analyseur.calculer_total_depenses_variables()} €")
print(f"Total des dépenses mensuelles : {analyseur.calculer_total_depenses_mensuelles()} €")
print(f"Ratio des dépenses variables par rapport aux fixes : {analyseur.calculer_ratio_depenses_variables()}")
