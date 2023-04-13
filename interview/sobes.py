# ----------------------------------------------------
Get users who attended some activity more than twice.

"""
SELECT * FROM users;

user_id  username
1        John Doe
2        Jane Don
3        Alice Jones
4        Lisa Romero

SELECT * FROM training_details;

user_training_id  user_id  training_id  training_date
1                 1        1            "2015-08-02"
2                 2        1            "2015-08-03"
3                 3        2            "2015-08-02"
4                 4        2            "2015-08-04"
5                 2        2            "2015-08-03"
6                 1        1            "2015-08-02"
7                 3        2            "2015-08-04"
8                 4        3            "2015-08-03"
9                 1        4            "2015-08-03"
10                3        1            "2015-08-02"
11                4        2            "2015-08-04"
12                3        2            "2015-08-02"
13                1        1            "2015-08-02"
14                4        3            "2015-08-03"
"""

select user_id, username, count(user_training_id)
from training_details td
left join user u ON u.user_id = td.user_id 
group by 1,2 
having count(user_training_id) > 2



"""
Implement a group_by_owners that:
Accepts a dictionary containing the file owner name for each file name.
Returns a dictionary containing a list of file names for each owner name, in any order.
For example, dictionary:
{
    'Input.txt': 'Randy',
    'Code.py': 'Stan',
    'Output.txt': 'Randy'
}

the group_by_owners function should return:
{
    'Randy': ['Input.txt', 'Output.txt'],
    'Stan': ['Code.py']
}
"""

def group_by_owners(files):
	out = {}
    for filename, owner in files.output():
    	if owner in out:
      	out[owner].append(filename)
      else:
      	out[onwer] = [filename]
    return out

files = {
    'Input.txt': 'Randy',
    'Code.py': 'Stan',
    'Output.txt': 'Randy'
}
print(group_by_owners(files))

#------------------------------------------------------
"""
Sort list of objects by age field in ascending order
"""

"""
Input:
"""

animals = [
    {'type': 'penguin', 'name': 'Stephanie', 'age': 8},
    {'type': 'elephant', 'name': 'Devon', 'age': 3},
    {'type': 'puma', 'name': 'Moe', 'age': 5},
]

"""
Output:
"""

sorted_animals = [
    {'type': 'elephant', 'name': 'Devon', 'age': 3},
    {'type': 'puma', 'name': 'Moe', 'age': 5},
    {'type': 'penguin', 'name': 'Stephanie', 'age': 8}
]

animals.sort(key=lambda x: x['age'])

#  --------------------------
"""
What will be the output of the following code snippet?
"""
class Person:
    def __init__(self, id):
        self.id = id

sam = Person(100)                  # sam: {id: 100}
sam.__dict__['age'] = 49           # sam: {id: 100, age: 49}
print(sam.age + len(sam.__dict__)) # out: 49+2 = 51


# --------------------------------------
"""
What will be the output of the following code snippet?
"""

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

