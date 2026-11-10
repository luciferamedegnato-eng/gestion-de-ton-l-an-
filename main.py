from pathlib import Path

import database


DATABASE_PATH = Path(__file__).resolve().parent / "data" / "depenses.db"
CATEGORIES = (
    "Alimentation",
    "Transport",
    "Logement",
    "Santé",
    "Loisirs",
    "Autre",
)


def afficher_menu():
    print("\n=== Gestionnaire de dépenses personnelles ===")
    print("1. Ajouter de l'argent")
    print("2. Enregistrer une dépense")
    print("3. Consulter mon solde")
    print("4. Voir l'historique")
    print("5. Voir les statistiques")
    print("6. Quitter")


def formater_montant(montant_xof):
    return f"{montant_xof:,}".replace(",", " ") + " FCFA"


def saisir_montant(libelle):
    saisie = input(f"Montant {libelle} en FCFA (nombre entier) : ").strip()
    try:
        montant_xof = int(saisie.replace(" ", "").replace("\u202f", ""))
    except ValueError:
        print("Montant invalide. Saisissez un nombre entier positif en FCFA.")
        return None

    if montant_xof <= 0:
        print("Le montant doit être strictement positif.")
        return None

    if montant_xof > database.MAX_AMOUNT_XOF:
        print("Le montant est trop élevé.")
        return None

    return montant_xof


def saisir_description():
    description = input("Description : ").strip()
    if not description:
        print("La description ne peut pas être vide.")
        return None
    return description


def ajouter_revenu():
    montant = saisir_montant("du revenu")
    if montant is None:
        return

    description = saisir_description()
    if description is None:
        return

    database.add_income(DATABASE_PATH, montant, description)
    print(f"Revenu de {formater_montant(montant)} ajouté avec succès.")


def ajouter_depense():
    montant = saisir_montant("de la dépense")
    if montant is None:
        return

    print("Catégories :")
    for numero, categorie in enumerate(CATEGORIES, start=1):
        print(f"{numero}. {categorie}")
    choix_categorie = input("Choisissez une catégorie : ").strip()
    if not choix_categorie.isdigit() or not 1 <= int(choix_categorie) <= len(CATEGORIES):
        print("Catégorie invalide.")
        return
    categorie = CATEGORIES[int(choix_categorie) - 1]

    description = saisir_description()
    if description is None:
        return

    try:
        database.add_expense(DATABASE_PATH, montant, description, categorie)
    except database.InsufficientBalanceError:
        print("Dépense refusée : votre solde est insuffisant.")
        return
    print(f"Dépense de {formater_montant(montant)} ajoutée avec succès.")


def afficher_solde():
    solde = database.get_balance(DATABASE_PATH)
    print(f"Votre solde actuel est de {formater_montant(solde)}.")


def afficher_historique():
    transactions = database.get_history(DATABASE_PATH)
    if not transactions:
        print("Aucune transaction enregistrée.")
        return

    print("\n=== Historique des transactions ===")
    for transaction in transactions:
        type_operation = "Revenu" if transaction["type"] == "income" else "Dépense"
        categorie = (
            f" | Catégorie : {transaction['category']}"
            if transaction["category"]
            else ""
        )
        print(
            f"{transaction['operation_date']} | {type_operation} | "
            f"{formater_montant(transaction['amount_xof'])} | "
            f"{transaction['description']}{categorie}"
        )


def afficher_statistiques():
    print("\n1. Par jour")
    print("2. Par mois")
    print("3. Par année")
    choix = input("Choisissez une période : ").strip()
    periodes = {"1": "jour", "2": "mois", "3": "année"}
    periode = periodes.get(choix)
    if periode is None:
        print("Période invalide.")
        return

    statistiques = database.get_statistics(DATABASE_PATH, periode)
    if not statistiques["periods"]:
        print("Aucune transaction à analyser.")
        return

    print(f"\n=== Totaux par {periode} ===")
    for ligne in statistiques["periods"]:
        solde = ligne["income_xof"] - ligne["expense_xof"]
        print(
            f"{ligne['period']} | Revenus : "
            f"{formater_montant(ligne['income_xof'])} | Dépenses : "
            f"{formater_montant(ligne['expense_xof'])} | Solde net : "
            f"{formater_montant(solde)}"
        )

    if statistiques["categories"]:
        print("\nDépenses par catégorie (toutes périodes confondues) :")
        for ligne in statistiques["categories"]:
            print(
                f"{ligne['category']} : "
                f"{formater_montant(ligne['expense_xof'])}"
            )


def main():
    database.init_db(DATABASE_PATH)

    while True:
        afficher_menu()
        choix = input("Votre choix : ").strip()

        if choix == "1":
            ajouter_revenu()
        elif choix == "2":
            ajouter_depense()
        elif choix == "3":
            afficher_solde()
        elif choix == "4":
            afficher_historique()
        elif choix == "5":
            afficher_statistiques()
        elif choix == "6":
            print("Au revoir !")
            break
        else:
            print("Choix invalide. Veuillez sélectionner un nombre de 1 à 6.")


if __name__ == "__main__":
    main()
