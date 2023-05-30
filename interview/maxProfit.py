
# 1st stupid
def maxProfit(prices: list[int]) -> int:
    max_profit = 0
    l = len(prices)
    for i, price in enumerate(prices):
        if i > 0 and price >= prices[i-1]:
            continue
        if i == l:
            continue
        if i == l -1:
            best_price = prices[-1]
        else:
            best_price = max(prices[i+1:])
        if best_price - price > max_profit:
            max_profit = best_price - price
    return max_profit


print(maxProfit([7,6,4,3,1]))


# 2nd clever using nodes

class Node:
    def __init__(self):
        self.lowest_left = 0
        self.highest_right = 0


class Solution:
    def maxProfit(self, prices: list[int]) -> int:
        nodes = [Node() for _ in range(len(prices))]

        lowest_left = prices[0]
        for i, p in enumerate(prices):
            node = nodes[i]
            if p < lowest_left:
                lowest_left = p
            node.lowest_left = lowest_left
            
        highest_right = prices[-1]
        for i, p in enumerate(reversed(prices)):
            node = nodes[len(prices) - i -1]
            if p > highest_right:
                highest_right = p
            node.highest_right = highest_right

        return max(max(node.highest_right - node.lowest_left for node in nodes), 0)

print(Solution.maxProfit(None, [3,2,6,5,0,3]))


# 3rd nodes minus iteration
class Node:
    def __init__(self):
        self.lowest_left = 0


class Solution:
    def maxProfit(self, prices: list[int]) -> int:
        max_profit = 0

        nodes = [Node() for _ in range(len(prices))]

        lowest_left = prices[0]
        for i, p in enumerate(prices):
            node = nodes[i]
            if p < lowest_left:
                lowest_left = p
            node.lowest_left = lowest_left
            
        highest_right = prices[-1]
        for i, p in enumerate(reversed(prices)):
            node = nodes[len(prices) - i -1]
            if p > highest_right:
                highest_right = p
            profit = highest_right - node.lowest_left
            if profit > max_profit:
                max_profit = profit
        return max_profit

# from internet, single iteration with windows
class Solution:
    def maxProfit(self, prices: list[int]) -> int:
        left = 0 #Buy
        right = 1 #Sell
        max_profit = 0
        while right < len(prices):
            currentProfit = prices[right] - prices[left] #our current Profit
            if prices[left] < prices[right]:
                max_profit =max(currentProfit,max_profit)
            else:
                left = right
            right += 1
        return max_profit