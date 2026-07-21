import os
import unittest

os.environ["GENAI_KEY"] = "fake-key"
os.environ["SPOONACULAR_KEY"] = "fake-key"

import ruby


# unit test function
class TestRuby(unittest.TestCase):
    # Checking no results
    def test_search_food_no_results(self):
        res = ruby.search_food("qwertyuiop")

        self.assertIsNone(res)
    
    # check no result for drug
    def test_drug_no_result(self):
        res = ruby.search_drug("asdfghjkl", price="N/A")

        self.assertIsNone(res)

    # test no result for cosmetic
    def test_cosmetic_no_result(self):
        res = ruby.search_cosmetic("asdfghjkl", price="N/A")

        self.assertIsNone(res)
    
    #test getting the get_link
    def test_get_link(self):
      res = ruby.get_link("advil")
      self.assertEqual(res, "https://www.google.com/search?q=buy+advil")

    #test gemini gives a list of foods
    def test_generate_food_remedies(self):
      res = ruby.generate_food_remedies("i want to lose weight")
      self.assertEqual(type(res), list)

    #test gemini gives a list of drugs
    def test_generate_drug_remedies(self):
        res = ruby.generate_drug_remedies("i have a headache")
        self.assertEqual(type(res), list)

    #test gemini gives a list of cosmetics
    def test_generate_cosmetic_remedies(self):
        res = ruby.generate_cosmetic_remedies("what do i do if i have acne")
        self.assertEqual(type(res), list)

    #test searching a food gives a result
    def test_search_food_has_results(self):
      res = ruby.search_food("chicken")
      self.assertIsNotNone(res)
      self.assertTrue(len(res) > 0)
    
    #test searching a ddrug gives a result
    def test_search_drug_has_results(self):
      res = ruby.search_drug("advil")
      self.assertIsNotNone(res)
      self.assertTrue(len(res) > 0)
    
    #test searching a cosmetic gives a result
    def test_search_cosmetic_has_results(self):
      res = ruby.search_cosmetic("foundation")
      self.asserIsNotNone(res)
      self.assertTrue(len(res) > 0)









    def test_search_drug_is_not_none(self):
        response= recaller.search_drug("Nyquil")
        self.assertIsNotNone(response)

    def test_search_cosmetic_is_not_none(self):
        response= recaller.search_cosmetics("Cerave")
        self.assertIsNotNone(response)

    def test_search_food_is_not_none(self):
        response= recaller.search_food("Coke")
        self.assertIsNotNone(response)




if __name__ == "__main__":
    unittest.main()