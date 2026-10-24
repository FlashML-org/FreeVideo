"""./freevideo agent ... — the agent API from a shell; every command prints JSON.

    ./freevideo agent status
    ./freevideo agent submit shot.json --wait
    ./freevideo agent batch storyboard.json --wait
    ./freevideo agent job j0123456789ab

Linux x86_64 uses ./freevideo; Windows uses freevideo.cmd. On macOS and other
Unix platforms, use the installed interpreter and source folder:
    env FREEVIDEO_AGENT=1 PYTHONPATH=/path/to/FreeVideo/engine-source \
        /path/to/installed/python -X utf8 -m freevideo_engine.agent.cli --root /path/to/FreeVideo status
"""
import argparse
import json
from pathlib import Path
import sys

from .client import ApiError, Client
from . import schema, spec, errors


def read(path):
    text = sys.stdin.read() if path == '-' else Path(path).read_text(encoding='utf-8')
    value = json.loads(text)
    schema.validate({'type': 'object'}, value)
    return value


EXTRA_COMMANDS = {
    'inspect': ('POST', '/media/inspect'), 'contact-sheet': ('POST', '/media/contact-sheet'),
    'edit': ('POST', '/media/edit'), 'library': ('GET', '/library'),
    'queue': ('GET', '/queue'), 'hello': ('POST', '/connect/hello'),
    'connection-status': ('GET', '/connect/status'), 'openapi': ('GET', '/openapi.json'),
}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, errors.text(errors.build('invalid_request')) + '\n')


def build_parser():
    parser = Parser(prog='./freevideo agent', description='Drive FreeVideo through its agent API.')
    parser.add_argument('--root', type=Path)
    parser.add_argument('--url')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status', help='Readiness, limits, LoRAs, VLM and queue')
    commands.add_parser('guide', help='How to write H3 prompts')
    commands.add_parser('schema', help='JSON Schemas of jobs')
    command = commands.add_parser('install-skill', help='Copy the FreeVideo skill where Codex, Gemini CLI, Cursor and Claude Code read skills')
    command.add_argument('--target', choices=('agents', 'claude'), action='append', help='Default: both')
    command = commands.add_parser('capabilities', help='Read the studio capability cache')
    schema.cli_arguments(command, schema.contracts()['GET', '/capabilities']['query'])
    command = commands.add_parser('asset', help='Download a saved library video or thumbnail over HTTP')
    command.add_argument('id')
    command.add_argument('target', type=Path)
    command.add_argument('--thumbnail', action='store_true')
    for name, text in (('plan', 'Validate a job file and estimate it'), ('submit', 'Submit a job file (- = stdin)'),
                       ('batch', 'Submit {"jobs": [...]} with @ref links')):
        command = commands.add_parser(name, help=text)
        command.add_argument('file')
        if name != 'plan':
            command.add_argument('--wait', action='store_true', help='Wait for the job(s) and print the results')
        if name in ('submit', 'plan'):
            schema.cli_arguments(command, schema.generate(local=True))
        else:
            schema.cli_arguments(command, schema.batch_schema(local=True), exclude=('jobs',))
    for name in ('job', 'wait', 'cancel', 'resume'):
        command = commands.add_parser(name)
        command.add_argument('id')
        if name == 'wait':
            schema.cli_arguments(command, schema.obj({'timeout': schema.CLIENT_WAIT}))
        elif name == 'job':
            schema.cli_arguments(command, schema.contracts()['GET', '/jobs/{id}']['query'])
        elif name == 'resume':
            schema.cli_arguments(command, schema.contracts()['POST', '/jobs/{id}/resume']['body'])
    command = commands.add_parser('jobs')
    schema.cli_arguments(command, schema.contracts()['GET', '/jobs']['query'])
    command = commands.add_parser('upload', help='Copy a local media file into FreeVideo inputs')
    command.add_argument('path')
    command = commands.add_parser('imports', help='Bring local video or audio into the editing timeline')
    imports = command.add_subparsers(dest='operation', required=True)
    command = imports.add_parser('add', help='Upload content; poll imports list until ready before insert')
    command.add_argument('path')
    schema.cli_arguments(command, schema.obj({'ref': schema.REF}))
    imports.add_parser('list', help='List imported sources and preparation progress')
    command = commands.add_parser('generate-image', help='Generate or change a picture with the local image model')
    schema.cli_arguments(command, schema.image(), exclude=('kind',))
    command.add_argument('--wait', action='store_true', help='Wait for the picture and print the result')
    command = commands.add_parser('drama', help='Plan, start, review and finish a durable film')
    drama = command.add_subparsers(dest='operation', required=True)
    for name, (method, path, summary) in schema.DRAMA_OPERATIONS.items():
        sub = drama.add_parser(name, help=summary)
        if '{id}' in path:
            sub.add_argument('id')
        if '{shot}' in path:
            sub.add_argument('shot')
        if name in ('plan', 'start'):
            sub.add_argument('file', help='JSON production request with screenplay (- = stdin)')
        schema.cli_arguments(sub, schema.drama_schema(name))
    command = commands.add_parser('cast', help='Find, create and change character and setting cards')
    cast = command.add_subparsers(dest='operation', required=True)
    for name, (method, path, summary) in schema.CAST_OPERATIONS.items():
        sub = cast.add_parser(name, help=summary)
        if '{id}' in path:
            sub.add_argument('id')
        if name in ('create', 'update'):
            sub.add_argument('file', nargs='?', help='JSON card fields (- = stdin); options override them')
        schema.cli_arguments(sub, schema.cast_schema(name, local=True))
    command = commands.add_parser('projects', help='Read and edit timelines shared with the editor page')
    projects = command.add_subparsers(dest='operation', required=True)
    for name, (method, path, summary) in schema.PROJECT_OPERATIONS.items():
        sub = projects.add_parser(name, help=summary)
        if '{id}' in path:
            sub.add_argument('id')
        if name in ('create', 'write'):
            sub.add_argument('file', nargs='?', help='JSON {project, ref?} request (- = stdin); options override it')
        if name == 'operations':
            sub.add_argument('--operations-file', help='JSON array of operations (- = stdin)')
        schema.cli_arguments(sub, schema.project_schema(name))
    command = commands.add_parser('images', help='Edit pictures with the image editor\'s commands')
    images = command.add_subparsers(dest='operation', required=True)
    for name, (method, path, summary) in schema.IMAGE_OPERATIONS.items():
        sub = images.add_parser(name, help=summary)
        if '{id}' in path:
            sub.add_argument('id')
        if name in ('create', 'write'):
            sub.add_argument('file', nargs='?', help='JSON request (- = stdin); options override it')
        if name == 'operations':
            sub.add_argument('--operations-file', help='JSON array of operations (- = stdin)')
        schema.cli_arguments(sub, schema.image_schema(name))
    command = commands.add_parser('whitebox', help='Render a rough 3D white-model reference video in the user\'s open page')
    whitebox = command.add_subparsers(dest='operation', required=True)
    for name, (method, path, summary) in schema.WHITEBOX_OPERATIONS.items():
        sub = whitebox.add_parser(name, help=summary)
        if '{id}' in path:
            sub.add_argument('id')
        if name == 'submit':
            sub.add_argument('file', nargs='?', help='JSON {code, summary?} request (- = stdin); options override it')
        schema.cli_arguments(sub, schema.whitebox_schema(name))
    command = commands.add_parser('sessions', help='Share messages and plans with the user in a work conversation')
    sessions = command.add_subparsers(dest='operation', required=True)
    for name in ('list', 'create', 'events', 'post'):
        _, path, summary = schema.SESSION_OPERATIONS[name]
        sub = sessions.add_parser(name, help=summary)
        if '{id}' in path:
            sub.add_argument('id')
        if name == 'post':
            content = sub.add_mutually_exclusive_group(required=True)
            content.add_argument('--message', help='Text to send to the user')
            content.add_argument('--plan-file', help='JSON {shots, total_seconds?} plan (- = stdin)')
        else:
            schema.cli_arguments(sub, schema.session_schema(name))
    command = commands.add_parser('frame', help='Save a frame of a job or saved video')
    command.add_argument('source')
    schema.cli_arguments(command, schema.contracts()['POST', '/media/frame']['body'], exclude=('source',))
    for name, (method, path) in EXTRA_COMMANDS.items():
        command = commands.add_parser(name)
        definition = schema.contracts(local=True)[method, path]
        schema.cli_arguments(command, definition['query'] if method == 'GET' else definition['body'])
    return parser


def main(argv=None):
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = build_parser()
    args = parser.parse_args(argv)
    values = vars(args)

    def progress(view):
        value = view.get('progress') or {}
        line = '%s %s %s' % (view['id'], view['status'], value.get('label') or '')
        if value.get('fraction') is not None:
            line += ' %d%%' % round(100 * value['fraction'])
        print(line.rstrip(), file=sys.stderr, flush=True)

    def source(value):
        from .spec import JOB_ID, LIBRARY_ID
        return {'from_job': value} if JOB_ID.fullmatch(value) else {'library': value} if LIBRARY_ID.fullmatch(value) \
            else {'path': str(Path(value).resolve())} if Path(value).exists() else {'file': value}

    if args.command == 'install-skill':
        from . import skill_install
        print(json.dumps({'installed': skill_install.install(tuple(args.target or ('agents', 'claude')))}, indent=2))
        return 0

    try:
        client = Client(args.url, root=args.root, name='freevideo-cli')
        if args.command in EXTRA_COMMANDS:
            method, path = EXTRA_COMMANDS[args.command]
            definition = schema.contracts(local=True)[method, path]
            shape = definition['query'] if method == 'GET' else definition['body']
            value = {k: values[k] for k in shape['properties'] if k in values}
            schema.validate(shape, value)
            result = client.get(path, **value) if method == 'GET' else client.post(path, value)
        elif args.command == 'status':
            result = client.capabilities()
        elif args.command == 'guide':
            result = client.guide()
        elif args.command == 'schema':
            result = client.schema()
        elif args.command == 'capabilities':
            fields = schema.contracts()['GET', '/capabilities']['query']['properties']
            result = client.capability_table(**{k: values[k] for k in fields if k in values})
        elif args.command == 'asset':
            result = dict(id=args.id, path=str(client.asset(args.id, args.target, thumbnail=args.thumbnail)))
        elif args.command == 'plan':
            value = read(args.file) | {k: values[k] for k in schema.generate()['properties'] if k in values}
            schema.validate_job(value, local=True)
            schema.preflight_submission(value)
            result = client.plan(value)
        elif args.command in ('submit', 'batch'):
            fields = schema.batch_schema() if args.command == 'batch' else schema.generate()
            value = read(args.file) | {k: values[k] for k in fields['properties'] if k in values}
            schema.validate(schema.batch_schema(local=True) if args.command == 'batch' else schema.job_schema(local=True), value)
            schema.preflight_submission(value)
            jobs = client.batch(value['jobs'], **({'ref': value['ref']} if 'ref' in value else {})) if args.command == 'batch' else \
                client.submit_takes(value, value['takes']) if value.get('takes', 1) > 1 else [client.submit(value)]
            if args.wait:
                jobs = [client.wait(job['id'], on_progress=progress) for job in jobs]
            result = jobs[0] if len(jobs) == 1 and args.command == 'submit' else {'jobs': jobs}
        elif args.command == 'generate-image':
            value = dict(kind='image', **{k: values[k] for k in schema.image()['properties'] if k in values})
            schema.validate_job(value)
            result = client.submit(value)
            if args.wait:
                result = client.wait(result['id'], on_progress=progress)
        elif args.command == 'job':
            result = client.job(args.id, **({'wait': values['wait']} if 'wait' in values else {}))
        elif args.command == 'wait':
            result = client.wait(args.id, values.get('timeout', 25), on_progress=progress)
        elif args.command == 'cancel':
            result = client.cancel(args.id)
        elif args.command == 'resume':
            result = client.resume(args.id, **({'ref': values['ref']} if 'ref' in values else {}))
        elif args.command == 'jobs':
            result = {'jobs': client.jobs(**{k: values[k] for k in schema.contracts()['GET', '/jobs']['query']['properties'] if k in values})}
        elif args.command == 'drama':
            method, path, _ = schema.DRAMA_OPERATIONS[args.operation]
            shape = schema.drama_schema(args.operation)
            value = read(args.file) if args.operation in ('plan', 'start') else {}
            value.update({k: values[k] for k in shape['properties'] if k in values})
            schema.validate(shape, value)
            for key in ('id', 'shot'):
                if '{' + key + '}' in path:
                    schema.validate(schema.PRODUCTION if key == 'id' else schema.SLUG, values[key])
                    path = path.replace('{' + key + '}', values[key])
            result = client.get(path, **value) if method == 'GET' else client.post(path, value)
        elif args.command == 'cast':
            method, path, _ = schema.CAST_OPERATIONS[args.operation]
            shape = schema.cast_schema(args.operation, local=True)
            value = read(args.file) if getattr(args, 'file', None) else {}
            value.update({k: values[k] for k in shape['properties'] if k in values})
            schema.validate(shape, value)
            if '{id}' in path:
                schema.validate(schema.CAST, values['id'])
                path = path.replace('{id}', values['id'])
            result = client.get(path, **value) if method == 'GET' else client.send(method, path, value)
        elif args.command in ('projects', 'images', 'whitebox'):
            table, shape_of, ident = {'projects': (schema.PROJECT_OPERATIONS, schema.project_schema, schema.PROJECT),
                                      'images': (schema.IMAGE_OPERATIONS, schema.image_schema, schema.IMAGE),
                                      'whitebox': (schema.WHITEBOX_OPERATIONS, schema.whitebox_schema, schema.WHITEBOX)}[args.command]
            method, path, _ = table[args.operation]
            shape = shape_of(args.operation)
            value = read(args.file) if getattr(args, 'file', None) else {}
            value.update({k: values[k] for k in shape['properties'] if k in values})
            if getattr(args, 'operations_file', None):
                text = sys.stdin.read() if args.operations_file == '-' else Path(args.operations_file).read_text(encoding='utf-8')
                value['operations'] = json.loads(text)
            schema.validate(shape, value)
            if '{id}' in path:
                schema.validate(ident, args.id)
                path = path.replace('{id}', args.id)
            result = client.get(path, **value) if method == 'GET' else client.send(method, path, value)
        elif args.command == 'sessions':
            method, path, _ = schema.SESSION_OPERATIONS[args.operation]
            shape = schema.session_schema(args.operation)
            if args.operation == 'post':
                if args.message is not None:
                    value = dict(type='message', text=args.message)
                else:
                    value = dict(type='plan') | read(args.plan_file)
                    schema.validate(schema.session_event(agent_only=True)['oneOf'][1], value)
                schema.validate(schema.session_event(agent_only=True), value)
            else:
                value = {k: values[k] for k in shape['properties'] if k in values}
            schema.validate(shape, value)
            if '{id}' in path:
                schema.validate(schema.SESSION, args.id)
                path = path.replace('{id}', args.id)
            result = client.get(path, **value) if method == 'GET' else client.post(path, value)
        elif args.command == 'upload':
            result = client.upload(args.path)
        elif args.command == 'imports':
            result = (client.import_media(args.path, **({'ref': args.ref} if hasattr(args, 'ref') else {}))
                      if args.operation == 'add' else client.get('/imports'))
        else:
            result = client.frame(source(args.source), values.get('at', 'last'))
    except ApiError as error:
        if error.inbox:
            print(json.dumps(dict(inbox=error.inbox), ensure_ascii=False, indent=1))
        print(str(error), file=sys.stderr)
        return 2
    except spec.SpecError as error:
        print(errors.text(errors.validation(error.errors)), file=sys.stderr)
        return 2
    except (ValueError, OSError):
        print(errors.text(errors.build('invalid_request')), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=1))
    failed = isinstance(result, dict) and result.get('status') in ('failed', 'cancelled', 'interrupted')
    if failed and result.get('error'):
        print(errors.text(result['error']), file=sys.stderr)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
