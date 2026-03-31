from app import app, process_due_contract_reminders


def main():
    with app.app_context():
        result = process_due_contract_reminders(limit=500)
        print(result)


if __name__ == '__main__':
    main()
