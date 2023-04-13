class A:
    def __init__(self):
        self.calcI(30)
        print("i from A is", self.i)

    def calcI(self, i):
        self.i = 2 * i


class B(A):
    def __init__(self):
        super().__init__()

    def calcI(self, i):
        self.i = 3 * i

b = B()

class Person:
    def __init__(self, id):
        self.id = id

sam = Person(100)                  # sam: {id: 100}
sam.__dict__['age'] = 49           # sam: {id: 100, age: 49}
print(sam.age + len(sam.__dict__)) # out: 49+2 = 51



def group_by_owners(files):
    out = {}
    for filename, owner in files.items():
        if owner in out:
            out[owner].append(filename)
        else:
            out[owner] = [filename]
    return out

files = {
    'Input.txt': 'Randy',
    'Code.py': 'Stan',
    'Output.txt': 'Randy'
}
print(group_by_owners(files))