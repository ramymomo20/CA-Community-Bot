import pandas

data = pandas.read_csv(r"iosna ratings.csv", header=0)
data1 = pandas.read_csv(r"bestna.csv", header=0)

class highest:
    def __init__(self, file):
        self.file = file

    def highestnames(self):
        name = list(self.file.Name)
        rate = list(self.file.Rating)

        return list(map(' '.join, zip(["1.","2.","3.","4.","5.","6.","7.","8.","9.","10."], 
                                      [item[0] for item in [[name[i]] for i in range(len(rate))]])))

    def highestpos(self):
        pos = list(self.file.Position)
        rate = list(self.file.Rating)

        highest_pos = [item[0] for item in [[pos[i]] for i in range(len(rate))]]

        return highest_pos

    def highestratings(self):
        rate = list(self.file.Rating)     
           
        highest_ratings =[item[0] for item in [[rate[i]] for i in range(len(rate))]]

        return highest_ratings