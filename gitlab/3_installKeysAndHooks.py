import csv
import gitlab
import codegrade
import re
import sys

def load_user_data(filename='webhooks.csv'):
    with open(filename) as user_data:
        reader = csv.DictReader(user_data)
        try:
            data = [ line for line in reader]
        except csv.Error as e:
            sys.exit('file {}, line {}: {}'.format(filename, reader.line_num, e))
    return data

def sanitise_reponame(reponame):
    # Replace all special characters with underscores
    result = re.sub(r'[^\w\-_]', '_', reponame, flags=re.ASCII)
    # Replace any duplicated underscores with a single underscore
    result = re.sub(r'__+', '_', result, flags=re.ASCII)
    # Remove any underscores at the start or end
    result = re.sub(r'(^_|_$)', '', result, flags=re.ASCII)
    return result

def sync(access, organization, roster, assignment, student_readable=False):
    print('Connecting to the group',organization['gitlab-group'],'...', end=' ', flush=True)
    g = gitlab.Gitlab(access["gitlab"]["host"], private_token=access["gitlab"]["token"])
    g.auth()
    me = g.users.get(g.user.id)
    #groups = g.groups.list(search=organization["gitlab-group"], order_by="similarity", get_all=True)
    root_group = g.groups.get(organization["gitlab-group"]) #groups[0]
    print("Using root group: " + root_group.web_url)
    student_group = None
    staff_group = None
    #for subgroup in root_group.descendant_groups.list(all=True):
    #    if subgroup.full_path == organization["gitlab-group"] + '/' + organization["subgroup-staff"] + '/' + assignment["subgroup"]:
    #        staff_group = g.groups.get(subgroup.id)
    staff_group = g.groups.get(organization["gitlab-group"] + '/' + organization["subgroup-staff"] + '/' + assignment["subgroup"])
    print("Using staff group: " + staff_group.web_url)
    #    if subgroup.full_path == organization["gitlab-group"] + '/' + organization["subgroup-students"]:
    #        root_student_group = g.groups.get(subgroup.id)
    root_student_group = g.groups.get(organization["gitlab-group"] + '/' + organization["subgroup-students"])
    print("Using root student group: " + root_student_group.web_url)
    #    if subgroup.full_path == organization["gitlab-group"] + '/' + organization["subgroup-students"] + '/' + assignment["subgroup"]:
    #        student_group = g.groups.get(subgroup.id)
    student_group = g.groups.get(organization["gitlab-group"] + '/' + organization["subgroup-students"] + '/' + assignment["subgroup"])
    print("Using student group: " + student_group.web_url)
    if student_group is None:
        raise LookupError("Could not find the student group, check organization dict entries and assignment.subgroup spelling!")
    if staff_group is None:
        raise LookupError("Could not find the staff group, check organization dict entries and assignment.subgroup spelling!")
    #g.groups.get(root_group.subgroups.list(search=organization["subgroup-students"] + '/' + assignment["subgroup"], order_by="similarity")[0].id, lazy=False) 
    
    print('done')
    print('Inviting students to the student group...')
    #invite_users(g, root_student_group)
    print('Loading the roster ...', end=' ', flush=True)
    group_info = load_user_data(roster)
    print('done')
    no_groups = 0
    no_errors = 0
    for group in group_info:
        reponame = assignment['gitlab-name'] + '-' + group['name']
        reponame = sanitise_reponame(reponame)
        group_members = group["git_ids"].split()
        print('Processing', reponame,'...')
        try:
            if reponame not in [ repo.path for repo in staff_group.projects.list(all=True) ]:
                print(">", "Repository", reponame, "does not exist yet, cloning...")
                template = None
                for repo in staff_group.projects.list(include_subgroups=True, all=True):
                    if repo.path == assignment["gitlab-name"]:
                        template = g.projects.get(repo.id, lazy=False)
                        break
                print(">", "Using template", template.path_with_namespace)
                if template is None:
                    raise LookupError("Did not find the template to clone, please check the spelling of assignment.gitlab-name!")
                fork = template.forks.create({'name': reponame, 'path': reponame, 'namespace': staff_group.full_path})
                print(">", "Repository", fork.path_with_namespace, "created successfully")
            repo = None
            for proj in staff_group.projects.list(all=True):
                if proj.path == reponame:
                    repo = g.projects.get(proj.id, lazy=False)
                    break
            if repo is None:
                raise LookupError("Did not find the template repository, check whether forking actually worked")
            while len(repo.protectedbranches.list()) > 0:
                def_branch = repo.protectedbranches.list()[0].name
                print(">", "Removing protection from the", def_branch, "branch of repository", repo.path_with_namespace)
                p_branch = repo.protectedbranches.get(def_branch)
                p_branch.delete()
            
            for member in group_members:
                print(">", "Processing collaborators:", member)
                if member in [ members.username for members in repo.members_all.list(get_all=True) ]:
                    print('>', 'Collaborator', member, 'already present')
                else:
                    print('>', 'Adding collaborator', member)
                    try:
                        # Find the user by id
                        member_id = g.users.list(username=member)[0].id
                        repo.members.create({'user_id': member_id, 'access_level': gitlab.const.AccessLevel.DEVELOPER})
                        print('>', 'Collaborator', member, 'added to the repository')
                    except:
                        e = sys.exc_info()[0]
                        print('>','Error:', e)
                        no_errors += 1
            #if repo.path in [ projects.path for projects in staff_group.projects.list(all=True) ]:
            #    print(">", "Staff already owns", reponame)
            #else:
            #    print(">", "Adding staff permission to maintain", reponame)
            #    repo.share(staff_group.id, gitlab.MAINTAINER_ACCESS)
            #    print(">", "Staff can now maintain", reponame)
            if student_readable:
                print(">","Checking if students can read the repo")
                if repo.path in [ projects.path for projects in student_group.projects.list(all=True) ]:
                    print(">", "Students can already see", reponame)
                else:
                    print(">", "Adding students permission to read", reponame)
                    repo.share(student_group.id, gitlab.const.AccessLevel.REPORTER)
                    print(">", "Students can now read", reponame)
            else:
                if repo.path in [ projects.path for projects in student_group.projects.list(all=True) ]:
                    print("> Students can read the repo, removing their access")
                    repo.unshare(student_group.id)
            
            if 'codegrade-key' not in [ key.title for key in repo.keys.list() ]:
                print('>','Adding deploy key for', group['name'])
                repo.keys.create({'title': 'codegrade-key', 'key': group['public_key']})
            else:
                print('>','Deploy key found for', group['name'])
            if group['payload_url'] not in [ hook.url for hook in repo.hooks.list() ]:
                print('>','Adding webhook for', group['name'])
                repo.hooks.create({'url': group['payload_url'], 'token': group['secret'], 'push_events': 1})
            else:
                print('>','Webhook found for', group['name'])
            no_groups += 1
        except Exception as exception:
            e = sys.exc_info()[0]
            print('>','Error:', e)
            print("Exception message: {}".format(exception))
            no_errors += 1
    print('\nProcessed',no_groups,'group(s);',no_errors,'error(s).')

def read_gitlab_ids(in_file):
    gitlab_ids = {}
    with open(in_file, 'r') as f:
        for row in csv.reader(f, delimiter=','):
            gitlab_ids[row[1]] = row[3]
    return gitlab_ids

def invite_users(g, student_group):
    gl_users = read_gitlab_ids("usernames.csv")
    gl_users = list(gl_users.values())
    gl_users = gl_users[1:]

    for member in gl_users:
        if member != "" and member not in [ members.username for members in student_group.members_all.list(get_all=True) ]:
            print("inviting", member, "to the student group")
            member_id = g.users.list(username=member)[0].id
            student_group.members.create({'user_id': member_id, 'access_level': gitlab.const.AccessLevel.REPORTER})

def main():
    with open("secrets.txt", "r") as secretfile:
        secrets = secretfile.read().splitlines()
    sync(
        access={
            'codegrade': {
                'host': "https://wur.codegra.de",
                'tenant': "Wageningen University",
                'username': secrets[0],
                'password': secrets[1],
            },
            'gitlab': {
                'host': "https://git.wur.nl",
                'token': secrets[2]
            }
        },
        organization={
            'codegrade-id': 7816,
            'gitlab-group': 'geoscripting-2024',
            'subgroup-staff': 'staff',
            'subgroup-students': 'students'
        },
        roster='webhooks.csv',
        assignment={
            'codegrade-id': 116843,
            'gitlab-name': 'Project_Starter',
            'subgroup': 'project'
        },
        student_readable=False
    )

    
if __name__ == '__main__':
    main()
