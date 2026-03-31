# Contract Reminders Scheduler

The recurring reminder logic is implemented in `process_due_contract_reminders` in `app.py`.

## How it works

- Contracts are stored in `contracts` with:
  - `frequency` (`weekly`, `fortnightly`, `monthly`)
  - `next_service_date`
  - `service_day`
  - `signer_name`
  - `terms_agreed`
- The reminder job checks **active contracts where `next_service_date == tomorrow`**.
- It sends a "day-before" reminder email.
- After sending, it advances the contract to the next cycle and recalculates:
  - `next_service_date`
  - `service_day`
  - `next_reminder_at`

## Scheduler options

### Option A: Cron (Linux)

Run daily at 09:00:

```bash
0 9 * * * /usr/bin/python /path/to/project/scripts/run_contract_reminders.py >> /path/to/project/logs/contract_reminders.log 2>&1
```

### Option B: Windows Task Scheduler

Create a daily task to run:

```powershell
python C:\Users\DeLL\OneDrive\Documents\Cleaning\scripts\run_contract_reminders.py
```

### Option C: Manual admin trigger

From Admin Dashboard → Contracts & Schedules → **Run Reminders Now**

This calls:

- `POST /admin/api/contracts/reminders/run`

## Notes

- The app also runs a throttled reminder pass while serving `/` and `/services`, but production should use a real scheduler (Cron/Task Scheduler) as the primary mechanism.
