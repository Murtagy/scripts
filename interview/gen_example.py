# task - load files and report progress

work = ['zip', 'mp3', 'avi']

def load_huge_ass_file(file):
    print('Loaded huge ass file', file)

### yield version
progress = 0


def load_huge_ass_files(file_names):
    for file in file_names:
        load_huge_ass_file(file)
        yield file


print(progress, '/ 3')

# MAIN LOOP is simple! here load_huge_ass_files reports us on work done
for file in load_huge_ass_files(work):
    print(progress, '/ 3')
    progress += 1

### non-yield
progress = 0

print(progress, '/ 3')
# MAIN LOOP is hard and non generic
for file in work:
    # here we have to do work ourselves
    load_huge_ass_file(file)
    progress += 1
    print(progress, '/ 3')

