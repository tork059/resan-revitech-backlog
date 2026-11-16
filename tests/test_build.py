import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build', ROOT / 'scripts/build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class DevelopmentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.patches = patch.multiple(build, ROOT=root, TASKS=root / 'tasks',
                                      ARCHIVE=root / 'archive', SITE=root / '_site')
        self.patches.start()
        self.addCleanup(self.patches.stop)
        self.clock = patch.object(build, 'today', return_value=dt.date(2026, 11, 16))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        build.TASKS.mkdir()
        build.WARNINGS.clear()

    def task(self, status='in-development', deadline='2026-11-10', priority='next'):
        p = build.TASKS / 'BL-900-test.md'
        text = ('---\nid: BL-900\ntitle: Проверка\nstatus: ' + status +
                '\npriority: ' + priority + '\ncreated: 2026-11-01\ndeadline: ' + deadline +
                '\nmoscow: should\n---\n\n## Описание\nСуществующие данные\n\n'
                '## Журнал\n- 2026-11-01 — создана\n')
        p.write_text(text)
        return build.parse_task(p)

    def test_development_survives_due_date_normalization_and_reload(self):
        for deadline in ('2026-11-10', '2026-11-16', '2026-11-20', ''):
            with self.subTest(deadline=deadline):
                task = self.task(deadline=deadline)
                build.normalize([task])
                loaded = build.parse_task(task['path'])
                self.assertEqual(loaded['status'], 'in-development')
                self.assertEqual(loaded['priority'], 'development')
                self.assertIn('Существующие данные', loaded['body'])
                self.assertNotIn('автоматически взята', loaded['body'])

    def test_planned_tasks_keep_manual_column_despite_deadlines_and_daily_builds(self):
        for priority in ('next', 'later'):
            for deadline in ('2026-10-01', '2026-11-16', '2026-11-20', '2027-12-01', ''):
                with self.subTest(priority=priority, deadline=deadline):
                    task = self.task(status='todo', deadline=deadline, priority=priority)
                    original = task['path'].read_text()
                    build.normalize([task])
                    with patch.object(build, 'today', return_value=dt.date(2028, 1, 1)):
                        task = build.parse_task(task['path'])
                        build.normalize([task])
                    self.assertEqual(task['status'], 'todo')
                    self.assertEqual(task['priority'], priority)
                    self.assertEqual(task['path'].read_text(), original)

    def test_waiting_migrates_once_without_changing_column_deadline_or_description(self):
        for priority in ('next', 'later'):
            task = self.task(status='waiting', deadline='2026-11-10', priority=priority)
            self.assertEqual(task['status'], 'todo')
            build.normalize([task])
            first = task['path'].read_text()
            self.assertIn('статус: Ожидание → Запланирована', first)
            task = build.parse_task(task['path'])
            build.normalize([task])
            self.assertEqual(task['path'].read_text(), first)
            self.assertEqual(task['priority'], priority)
            self.assertEqual(task['deadline'], '2026-11-10')
            self.assertIn('Существующие данные', task['body'])
        self.assertNotIn('waiting', build.STATUSES)
        self.assertNotIn('waiting', build.PLANNED)

    def test_development_can_be_completed_archived_and_reopened(self):
        task = self.task()
        build.normalize([task])
        build.set_field(task['path'], 'status', 'done')
        task = build.parse_task(task['path'])
        build.normalize([task])
        self.assertIn(build.ARCHIVE, task['path'].parents)
        build.set_field(task['path'], 'status', 'in-development')
        task = build.parse_task(task['path'])
        build.normalize([task])
        self.assertEqual(task['path'].parent, build.TASKS)
        self.assertEqual(task['closed'], '')
        self.assertEqual(task['priority'], 'development')

    def test_readme_lists_development_tasks_and_data_labels(self):
        task = self.task()
        build.normalize([task])
        readme = build.render_readme([task])
        section = readme.split('## 🛠 В разработке')[1].split('## 📅')[0]
        self.assertIn('BL-900', section)
        self.assertIn('in-development', build.STATUSES)
        self.assertIn('development', build.PRIORITIES)
        self.assertIn('in-development', build.PLANNED)

    def test_issue_creation_uses_description_and_accepts_legacy_label(self):
        for label in ('Описание', 'Суть и контекст'):
            with self.subTest(label=label):
                event = {'issue': {'number': 900, 'title': '[Задача] Проверка',
                                  'html_url': 'https://example.test/issue/900',
                                  'body': f'### Название\nПроверка\n\n### {label}\nВажные детали\n\n### Статус\nВ разработке — передано разработчикам'}}
                path = Path(self.tmp.name) / 'event.json'
                path.write_text(json.dumps(event))
                with patch.object(build, 'create_task') as create:
                    create.return_value = build.TASKS / 'BL-900-test.md'
                    build.from_issue(str(path))
                    body = create.call_args.kwargs['body']
                    self.assertEqual(create.call_args.kwargs['status'], 'in-development')
                    self.assertIn('## Описание\nВажные детали', body)
                    for removed in ('## Что сделать', '## Критерии готовности', '## Суть'):
                        self.assertNotIn(removed, body)

    def test_issue_preserves_explicit_later_column_with_overdue_deadline(self):
        event = {'issue': {'number': 901, 'title': '[Задача] План',
                          'html_url': 'https://example.test/issue/901',
                          'body': '### Статус\nЗапланирована\n\n### Колонка плана\nПотом\n\n### Срок\n01.10.2026'}}
        path = Path(self.tmp.name) / 'event.json'
        path.write_text(json.dumps(event))
        with patch.object(build, 'create_task') as create:
            create.return_value = build.TASKS / 'BL-901-test.md'
            build.from_issue(str(path))
            self.assertEqual(create.call_args.kwargs['status'], 'todo')
            self.assertEqual(create.call_args.kwargs['priority'], 'later')


if __name__ == '__main__':
    unittest.main()

